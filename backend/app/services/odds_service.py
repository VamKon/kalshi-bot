"""
OddsService — sportsbook odds integration via The Odds API.

Responsibilities:
1. Fetch real-time moneyline (h2h) odds from 40+ sportsbooks.
2. Cache results in PostgreSQL (sportsbook_odds table) with 6-hour TTL.
3. Match Kalshi markets to Odds API events by team names + commence time.
4. Compute consensus implied probability by averaging across bookmakers.
5. Detect line movement — flag significant moves (>3% shift since last cache).

The Odds API base URL:  https://api.the-odds-api.com/v4
Auth:                   ?apiKey=ODDS_API_KEY  (query parameter, no RSA signing)
Free tier:              500 requests/month — sufficient at 2 scans/day × 4 sports
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db_models import SportsbookOdds

logger = logging.getLogger(__name__)

# ── Competition name + format helpers ─────────────────────────────────────────

_COMPETITION_NAMES: dict[str, str] = {
    "cricket_ipl":                      "Indian Premier League",
    "cricket_big_bash":                 "Big Bash League",
    "cricket_psl":                      "Pakistan Super League",
    "cricket_caribbean_premier_league": "Caribbean Premier League",
    "cricket_sa20":                     "SA20",
    "cricket_ilt20":                    "ILT20",
    "cricket_the_hundred":              "The Hundred",
    "cricket_international_t20":        "International T20",
    "cricket_odi":                      "One Day International",
    "cricket_test_match":               "Test Match",
}


def _get_match_format(sport_key: str) -> str:
    if "test" in sport_key:
        return "Test"
    elif "odi" in sport_key:
        return "ODI"
    return "T20"


# ── Odds API constants ─────────────────────────────────────────────────────────

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Maps internal sport names → ONE OR MORE Odds API sport keys.
# Multiple keys are tried in order; whichever returns events gets used.
# Cricket needs several keys because Odds API splits formats (IPL, T20I, BBL, PSL…).
SPORT_KEY_MAP: dict[str, list[str]] = {
    "Cricket": [
        "cricket_ipl",                       # Indian Premier League (active April 2026)
        "cricket_international_t20",          # T20 internationals
        "cricket_psl",                        # Pakistan Super League
        "cricket_big_bash",                   # Big Bash League
        "cricket_odi",                        # ODI internationals
        "cricket_test",                       # Test matches
        "cricket_caribbean_premier_league",   # CPL
    ],
}

# Cache TTL in seconds — mirrors the news cache (6 hours)
ODDS_CACHE_TTL = 6 * 3600

# Line movement threshold — flag if consensus shifts more than this
LINE_MOVEMENT_THRESHOLD = 0.03


# ── Team alias map ─────────────────────────────────────────────────────────────
# Cricket teams only (NFL/NBA/MLS removed — bot is cricket-only).
# Bidirectional: short → full AND full → short.
# Used by _alias_tokens() so Odds API full names match Kalshi abbreviations.
# e.g. Kalshi title "Will SRH win?" → abbrev "srh" expands to "sunrisers hyderabad"
#      → matches Odds API home_team "Sunrisers Hyderabad".
# Add entries whenever a match failure is seen in the logs.
TEAM_ALIASES: dict[str, str] = {
    # IPL — short → full
    "srh":   "sunrisers hyderabad",
    "mi":    "mumbai indians",
    "rcb":   "royal challengers bengaluru",
    "csk":   "chennai super kings",
    "kkr":   "kolkata knight riders",
    "dc":    "delhi capitals",
    "pbks":  "punjab kings",
    "rr":    "rajasthan royals",
    "gt":    "gujarat titans",
    "lsg":   "lucknow super giants",
    # IPL — full → short
    "sunrisers hyderabad":          "srh",
    "mumbai indians":               "mi",
    "royal challengers bengaluru":  "rcb",
    "royal challengers bangalore":  "rcb",
    "chennai super kings":          "csk",
    "kolkata knight riders":        "kkr",
    "delhi capitals":               "dc",
    "punjab kings":                 "pbks",
    "rajasthan royals":             "rr",
    "gujarat titans":               "gt",
    "lucknow super giants":         "lsg",
    # PSL — short → full
    "kk":   "karachi kings",
    "lq":   "lahore qalandars",
    "ms":   "multan sultans",
    "pz":   "peshawar zalmi",
    "iq":   "islamabad united",
    "qs":   "quetta gladiators",
    # BBL — short → full
    "bsh":  "brisbane heat",
    "mlst": "melbourne stars",   # "mls" removed — collides with soccer abbreviation
    "mlr":  "melbourne renegades",
    "syd":  "sydney sixers",
    "syt":  "sydney thunder",
    "adl":  "adelaide strikers",
    "hob":  "hobart hurricanes",
    "per":  "perth scorchers",
}

# ── Venue map ──────────────────────────────────────────────────────────────────
# Cricket-only. Maps Odds API home_team (normalised) → venue string for AI prompt.
# Fallback: if home_team not in map, venue = "{home_team} home ground" (never blocks a trade).
VENUE_MAP: dict[str, str] = {
    # IPL home grounds
    "sunrisers hyderabad":          "Rajiv Gandhi International Cricket Stadium, Hyderabad",
    "mumbai indians":               "Wankhede Stadium, Mumbai",
    "royal challengers bengaluru":  "M. Chinnaswamy Stadium, Bengaluru",
    "royal challengers bangalore":  "M. Chinnaswamy Stadium, Bengaluru",
    "chennai super kings":          "MA Chidambaram Stadium, Chennai",
    "kolkata knight riders":        "Eden Gardens, Kolkata",
    "delhi capitals":               "Arun Jaitley Stadium, Delhi",
    "punjab kings":                 "PCA New International Cricket Stadium, Mullanpur",
    "rajasthan royals":             "Sawai Mansingh Stadium, Jaipur",
    "gujarat titans":               "Narendra Modi Stadium, Ahmedabad",
    "lucknow super giants":         "BRSABV Ekana Cricket Stadium, Lucknow",
    # T20 internationals — home_team IS the country name, venue varies; fallback is fine
}


# ── Venue lookup helper ────────────────────────────────────────────────────────

def get_venue(home_team: str) -> Optional[str]:
    """
    Return the venue string for a home team, or a generic fallback.

    Normalises name, resolves aliases (e.g. "SRH" → "sunrisers hyderabad"),
    then looks up VENUE_MAP.  Falls back to "{home_team} home ground" so the
    AI always gets some home-field context.  Returns None only when home_team
    is empty — never raises.
    """
    if not home_team:
        return None
    norm = _normalize_team(home_team)           # reuse the single normaliser below
    resolved = TEAM_ALIASES.get(norm, norm)     # expand abbrev if present
    venue = VENUE_MAP.get(resolved)
    if venue:
        return venue
    logger.debug(
        "get_venue: no entry for '%s' (norm='%s') — using generic fallback", home_team, norm
    )
    return f"{home_team} home ground"


# ── Probability helpers ────────────────────────────────────────────────────────

def _american_to_implied(odds: float) -> float:
    """Convert American odds to raw implied probability (before vig removal)."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def _remove_vig(home_raw: float, away_raw: float) -> tuple[float, float]:
    """
    Remove the bookmaker vig by normalising both sides to sum to 1.0.
    Returns (home_prob, away_prob) without vig.
    """
    total = home_raw + away_raw
    if total <= 0:
        return 0.5, 0.5
    return home_raw / total, away_raw / total


def _compute_consensus(bookmaker_probs: list[float]) -> float:
    """Average vig-removed probabilities across bookmakers for the consensus."""
    if not bookmaker_probs:
        return 0.5
    return round(sum(bookmaker_probs) / len(bookmaker_probs), 4)


# ── Team name matching helpers ─────────────────────────────────────────────────

def _normalize_team(name: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", "", name)
    return " ".join(name.split())


def _alias_tokens(team_norm: str) -> set[str]:
    """
    Return the set of tokens to match against a Kalshi title for this team.

    Three sources of tokens:
    1. The team's own words (len > 2): e.g. "sunrisers" "hyderabad" from "sunrisers hyderabad"
    2. The team's alias from TEAM_ALIASES (either direction):
       - full → short: "sunrisers hyderabad" → adds "srh"
       - short → full: "srh" → adds "sunrisers" "hyderabad"
    3. Per-word alias expansion: handles single-word abbreviations in multi-word team norms
       e.g. team_norm = "srh" → expands to ["sunrisers", "hyderabad"] and keeps "srh"

    All matching is done as exact word tokens (no substring) so "mi" never
    accidentally matches "semi", "limit", etc.
    """
    tokens: set[str] = {w for w in team_norm.split() if len(w) > 2}

    # Top-level alias (covers both full→short and short→full since the map is bidirectional)
    alias = TEAM_ALIASES.get(team_norm)
    if alias:
        alias_norm = _normalize_team(alias)
        # Add all words from the alias (short abbrevs like "srh" have len ≤ 2 but are
        # still valid match tokens — use len ≥ 2 floor here to keep them)
        tokens.update(w for w in alias_norm.split() if len(w) >= 2)

    # Per-word expansion: handles cases like team_norm = "srh" (single token, len=3)
    # where the top-level lookup already handled it, but also catches multi-word norms
    # that contain an abbreviation as one of their words.
    for word in team_norm.split():
        expanded = TEAM_ALIASES.get(word)
        if expanded:
            tokens.update(w for w in _normalize_team(expanded).split() if len(w) > 2)
            tokens.add(word)   # keep the abbreviation itself as a matchable token

    return tokens


def _teams_overlap(kalshi_title: str, event_home: str, event_away: str) -> bool:
    """
    Return True if the Kalshi market title appears to reference the same game.

    Uses alias-aware token overlap so IPL abbreviations in Kalshi titles
    (e.g. "SRH", "MI", "RCB") correctly match Odds API full team names
    (e.g. "Sunrisers Hyderabad", "Mumbai Indians").

    Uses exact word matching only (t in title_words) — never substring matching —
    to prevent short tokens like "mi" matching inside words like "semi" or "limit".
    """
    title_words = set(_normalize_team(kalshi_title).split())
    for team_name in (event_home, event_away):
        team_norm = _normalize_team(team_name)
        tokens = _alias_tokens(team_norm)
        if any(t in title_words for t in tokens):
            return True
    return False


# ── Main service ───────────────────────────────────────────────────────────────

class OddsService:
    """Fetches sportsbook odds and exposes consensus probability per event."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0)

    async def _fetch_odds(self, sport_key: str) -> list[dict]:
        """
        Fetch odds from The Odds API for one sport key.
        Returns raw list of event dicts, or [] on error / missing API key.
        A 404 means the sport key is not in the user's plan — logged at DEBUG.
        """
        if not settings.ODDS_API_KEY:
            logger.debug("ODDS_API_KEY not set — skipping odds fetch for %s", sport_key)
            return []

        url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
        params = {
            "apiKey":     settings.ODDS_API_KEY,
            "regions":    "us",
            "markets":    "h2h",
            "oddsFormat": "american",
        }
        try:
            resp = await self._client.get(url, params=params)
            if resp.status_code == 404:
                logger.debug("OddsAPI [%s]: sport key not available in plan (404)", sport_key)
                return []
            if resp.status_code == 422:
                logger.debug("OddsAPI [%s]: sport key not in-season (422)", sport_key)
                return []
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "OddsAPI [%s]: fetched %d events (remaining requests: %s)",
                sport_key, len(data),
                resp.headers.get("x-requests-remaining", "?"),
            )
            return data
        except httpx.HTTPStatusError as exc:
            logger.warning("OddsAPI HTTP error for %s: %s", sport_key, exc)
        except Exception as exc:
            logger.warning("OddsAPI fetch error for %s: %s", sport_key, exc)
        return []

    async def list_available_sport_keys(self) -> list[dict]:
        """
        Call GET /v4/sports to discover which sport keys are active in the plan.
        Useful for debugging — call once to find the right cricket key.
        """
        if not settings.ODDS_API_KEY:
            return []
        try:
            resp = await self._client.get(
                f"{ODDS_API_BASE}/sports",
                params={"apiKey": settings.ODDS_API_KEY},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("OddsAPI /sports list error: %s", exc)
            return []

    def _parse_event(self, event: dict) -> Optional[dict]:
        """
        Parse one Odds API event into a normalised dict with consensus_prob.

        Returns None if the event has no usable h2h bookmaker data.
        """
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence_time = event.get("commence_time", "")

        bookmakers = event.get("bookmakers", [])
        home_probs: list[float] = []
        away_probs: list[float] = []
        bookmaker_details: list[dict] = []

        for bm in bookmakers:
            bm_name = bm.get("title", bm.get("key", "unknown"))
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) < 2:
                    continue

                # Identify home / away by outcome name
                home_raw = away_raw = None
                for outcome in outcomes:
                    oname = outcome.get("name", "")
                    price = outcome.get("price")
                    if price is None:
                        continue
                    try:
                        price = float(price)
                    except (TypeError, ValueError):
                        continue

                    raw_imp = _american_to_implied(price)
                    if oname.lower() == home.lower():
                        home_raw = raw_imp
                    elif oname.lower() == away.lower():
                        away_raw = raw_imp

                if home_raw is not None and away_raw is not None:
                    h_prob, a_prob = _remove_vig(home_raw, away_raw)
                    home_probs.append(h_prob)
                    away_probs.append(a_prob)
                    bookmaker_details.append({
                        "bookmaker": bm_name,
                        "home_prob": round(h_prob, 4),
                        "away_prob": round(a_prob, 4),
                    })

        if not home_probs:
            return None

        consensus_home = _compute_consensus(home_probs)
        consensus_away = _compute_consensus(away_probs)

        return {
            "event_key":        event.get("id", ""),
            "sport_key":        event.get("sport_key", ""),   # carried through for match_format/competition
            "home_team":        home,
            "away_team":        away,
            "commence_time":    commence_time,
            "consensus_home":   consensus_home,
            "consensus_away":   consensus_away,
            "bookmaker_count":  len(bookmaker_details),
            "bookmakers":       bookmaker_details,
            "min_home_prob":    min(d["home_prob"] for d in bookmaker_details),
            "max_home_prob":    max(d["home_prob"] for d in bookmaker_details),
        }

    async def _load_cached(self, db: AsyncSession, sport: str) -> list[dict]:
        """
        Return one deduplicated event dict per event_key for a sport, using
        cached rows within the TTL window.  Reconstructs both home_team,
        away_team, consensus_home, and consensus_away from stored rows so
        match_market has everything it needs.
        Returns an empty list if cache is stale or missing.
        """
        now = datetime.now(timezone.utc)
        cutoff = now.timestamp() - ODDS_CACHE_TTL

        result = await db.execute(
            select(SportsbookOdds)
            .where(SportsbookOdds.sport == sport)
            .order_by(desc(SportsbookOdds.fetched_at))
            .limit(1000)
        )
        rows = result.scalars().all()

        fresh = [
            r for r in rows
            if r.fetched_at and r.fetched_at.timestamp() > cutoff
        ]

        # Deduplicate: one entry per event_key, preserving all team/consensus data.
        # Track per-bookmaker implied_prob values so we can reconstruct the actual
        # min/max range across bookmakers (previously both were set to consensus_prob,
        # losing all spread information when loading from cache).
        seen: dict[str, dict] = {}
        event_probs: dict[str, list[float]] = {}  # event_key → list of per-bm home probs

        for r in fresh:
            if r.event_key not in seen:
                seen[r.event_key] = {
                    "event_key":      r.event_key,
                    "sport_key":      r.sport or "",          # sport = e.g. "Cricket"
                    "home_team":      r.outcome or "",        # outcome = home team
                    "away_team":      r.away_team or "",      # stored since migration 004
                    "consensus_home": r.consensus_prob or 0.5,
                    "consensus_away": r.consensus_away or (1 - (r.consensus_prob or 0.5)),
                    "bookmaker_count": 0,
                    "bookmakers":     [],
                }
                event_probs[r.event_key] = []
            seen[r.event_key]["bookmaker_count"] += 1
            if r.implied_prob is not None:
                event_probs[r.event_key].append(float(r.implied_prob))

        # Compute min/max from actual stored per-bookmaker home probabilities
        for event_key, probs in event_probs.items():
            if probs:
                seen[event_key]["min_home_prob"] = round(min(probs), 4)
                seen[event_key]["max_home_prob"] = round(max(probs), 4)
            else:
                cp = seen[event_key]["consensus_home"]
                seen[event_key]["min_home_prob"] = cp
                seen[event_key]["max_home_prob"] = cp

        events = list(seen.values())
        return events

    async def _save_to_cache(
        self,
        db: AsyncSession,
        sport: str,
        market_id: str,
        parsed: dict,
    ) -> None:
        """Persist consensus odds into the sportsbook_odds cache table.

        Saves one row per bookmaker (for traceability) with both home and
        away team names so _load_cached can reconstruct full event dicts.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for bm in parsed.get("bookmakers", []):
            row = SportsbookOdds(
                market_id=market_id,
                event_key=parsed["event_key"],
                sport=sport,
                bookmaker=bm["bookmaker"],
                market_type="h2h",
                outcome=parsed["home_team"],           # home team name
                away_team=parsed.get("away_team", ""), # away team name (migration 004)
                price=None,
                implied_prob=bm["home_prob"],
                consensus_prob=parsed["consensus_home"],
                consensus_away=parsed.get("consensus_away"),  # away consensus (migration 004)
                fetched_at=now,
            )
            db.add(row)

        await db.commit()

    async def fetch_and_cache(self, db: AsyncSession, sport: str) -> list[dict]:
        """
        Fetch odds for a sport across all mapped sport keys, persist to DB,
        and return parsed events. Uses cached data if still within TTL.
        """
        sport_keys = SPORT_KEY_MAP.get(sport)
        if not sport_keys:
            logger.debug("No Odds API mapping for sport '%s'", sport)
            return []

        # Try cache first
        cached = await self._load_cached(db, sport)
        if cached:
            logger.info("OddsService [%s]: using cached odds (%d rows)", sport, len(cached))
            return cached

        # Cache miss — fetch live across all keys for this sport
        parsed_events: list[dict] = []
        for sport_key in sport_keys:
            raw_events = await self._fetch_odds(sport_key)
            for event in raw_events:
                parsed = self._parse_event(event)
                if parsed:
                    await self._save_to_cache(db, sport, parsed["event_key"], parsed)
                    parsed_events.append(parsed)

        logger.info(
            "OddsService [%s]: fetched & cached %d usable events across %d keys",
            sport, len(parsed_events), len(sport_keys)
        )
        return parsed_events

    def match_market(
        self,
        market: dict,
        events: list[dict],
        side: str = "yes",
    ) -> Optional[dict]:
        """
        Match a Kalshi market to an Odds API event by team name overlap.

        Determines which team the Kalshi YES side refers to by scoring how many
        words from each team's name appear in the market title.  Kalshi titles
        are phrased "Will [Team] win?" so the named team is always the YES side.
        This correctly handles away-team markets (e.g. "Will DC win?" when DC is
        the away team) that the old code mis-mapped by always assuming YES=home.

        Returns a dict with:
          - consensus_prob      (probability for the requested side)
          - consensus_home_prob / consensus_away_prob
          - yes_is_home         (True if the YES team is the Odds API home team)
          - bookmaker_count
          - bookmakers          (list of per-bookmaker probs)
          - min_prob / max_prob (bookmaker range for the requested side)

        Returns None if no matching event is found.
        """
        title = market.get("title", "")
        ticker = market.get("ticker", "")
        logger.debug("match_market: checking %d events for [%s] '%s'", len(events), ticker, title)

        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            logger.debug("  candidate event: home='%s' away='%s'", home, away)

            if not _teams_overlap(title, home, away):
                continue

            consensus_home = event.get("consensus_home", 0.5)
            consensus_away = event.get("consensus_away", round(1 - consensus_home, 4))
            min_home = event.get("min_home_prob", consensus_home)
            max_home = event.get("max_home_prob", consensus_home)

            # Determine which team the Kalshi YES side refers to.
            #
            # Primary: read the YES team directly from the ticker suffix.
            # Kalshi game-winner tickers follow the pattern:
            #   KXIPLGAME-{DATE}{AWAY}{HOME}-{YES_TEAM}
            # The last segment after the final "-" is the YES team abbreviation.
            # e.g. KXIPLGAME-26APR06PBKSKKR-PBKS → YES=PBKS (away), yes_is_home=False
            #      KXIPLGAME-26APR06PBKSKKR-KKR  → YES=KKR  (home), yes_is_home=True
            #
            # Fallback: if the ticker suffix doesn't resolve to a known team,
            # fall back to title token scoring (old behaviour).
            yes_is_home: bool  # assigned below in all branches
            ticker_parts = ticker.rsplit("-", 1)
            yes_team_abbrev = _normalize_team(ticker_parts[-1]) if len(ticker_parts) == 2 else ""
            home_tokens = _alias_tokens(_normalize_team(home))
            away_tokens = _alias_tokens(_normalize_team(away))

            if yes_team_abbrev and yes_team_abbrev in home_tokens:
                yes_is_home = True
                logger.debug("  ticker suffix '%s' matched HOME team → yes_is_home=True", yes_team_abbrev)
            elif yes_team_abbrev and yes_team_abbrev in away_tokens:
                yes_is_home = False
                logger.debug("  ticker suffix '%s' matched AWAY team → yes_is_home=False", yes_team_abbrev)
            else:
                # Fallback: title token scoring
                title_words = set(_normalize_team(title).split())
                home_score  = sum(1 for t in home_tokens if t in title_words)
                away_score  = sum(1 for t in away_tokens if t in title_words)
                yes_is_home = away_score <= home_score
                logger.debug(
                    "  ticker suffix '%s' unresolved — falling back to title scoring: "
                    "home_score=%d away_score=%d yes_is_home=%s",
                    yes_team_abbrev, home_score, away_score, yes_is_home,
                )

            # Map requested side to the correct consensus probability and range.
            #
            #   yes_is_home=True:   YES↔home, NO↔away
            #   yes_is_home=False:  YES↔away, NO↔home
            #
            # min/max for the away side are derived by inverting the home range
            # (away_prob = 1 - home_prob for every bookmaker after vig removal).
            if side == "yes":
                if yes_is_home:
                    consensus_prob = consensus_home
                    min_prob       = min_home
                    max_prob       = max_home
                else:
                    consensus_prob = consensus_away
                    min_prob       = round(1 - max_home, 4)
                    max_prob       = round(1 - min_home, 4)
            else:  # side == "no"
                if yes_is_home:
                    consensus_prob = consensus_away
                    min_prob       = round(1 - max_home, 4)
                    max_prob       = round(1 - min_home, 4)
                else:
                    consensus_prob = consensus_home
                    min_prob       = min_home
                    max_prob       = max_home

            # Derive venue from the home team name.
            # get_venue() never raises — returns a generic fallback if the team
            # isn't in VENUE_MAP, and None only when home is empty.
            venue = get_venue(home)

            # Derive competition name and match format from event's sport_key if available
            sport_key   = event.get("sport_key", "")
            competition = _COMPETITION_NAMES.get(sport_key, "")
            match_format = _get_match_format(sport_key)

            return {
                "consensus_prob":      round(consensus_prob, 4),
                "consensus_home_prob": round(consensus_home, 4),
                "consensus_away_prob": round(consensus_away, 4),
                "yes_is_home":         yes_is_home,
                "bookmaker_count":     event.get("bookmaker_count", 0),
                "bookmakers":          event.get("bookmakers", []),
                "min_prob":            round(min_prob, 4),
                "max_prob":            round(max_prob, 4),
                "home_team":           home,
                "away_team":           away,
                "venue":               venue,       # str or None; None when home is empty
                "event_key":           event.get("event_key", ""),
                "competition":         competition,
                "match_format":        match_format,
            }

        logger.debug("match_market: no match found for '%s'", title)
        return None

    def describe_movement(self, current_prob: float, prior_prob: Optional[float]) -> str:
        """
        Return a human-readable description of line movement.
        """
        if prior_prob is None:
            return "No prior reading"
        delta = current_prob - prior_prob
        if abs(delta) < LINE_MOVEMENT_THRESHOLD:
            return f"Stable (Δ{delta:+.1%})"
        direction = "up" if delta > 0 else "down"
        return f"Line moved {direction} {abs(delta):.1%} — significant shift"

    async def close(self) -> None:
        await self._client.aclose()


odds_service = OddsService()
