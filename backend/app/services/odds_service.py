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

# ── Odds API constants ─────────────────────────────────────────────────────────

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Maps internal sport names → ONE OR MORE Odds API sport keys.
# Multiple keys are tried in order; whichever returns events gets used.
# Cricket needs several keys because Odds API splits formats (IPL, T20I, BBL, PSL…).
SPORT_KEY_MAP: dict[str, list[str]] = {
    "NFL":     ["americanfootball_nfl"],
    "NBA":     ["basketball_nba"],
    "MLS":     ["soccer_usa_mls"],
    "Cricket": [
        "cricket_international_t20",   # T20 internationals (e.g. NZ vs SA)
        "cricket_ipl",                 # Indian Premier League
        "cricket_big_bash",            # Big Bash League
        "cricket_psl",                 # Pakistan Super League
        "cricket_odi",                 # ODI internationals
        "cricket_test",                # Test matches
        "cricket_caribbean_premier_league",
    ],
}

# Cache TTL in seconds — mirrors the news cache (6 hours)
ODDS_CACHE_TTL = 6 * 3600

# Line movement threshold — flag if consensus shifts more than this
LINE_MOVEMENT_THRESHOLD = 0.03


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


def _teams_overlap(kalshi_title: str, event_home: str, event_away: str) -> bool:
    """
    Return True if the Kalshi market title appears to reference the same game.
    Uses a simple token-overlap heuristic on normalized team names.
    """
    title_norm  = _normalize_team(kalshi_title)
    home_norm   = _normalize_team(event_home)
    away_norm   = _normalize_team(event_away)

    # Check if any word from home or away team appears in the Kalshi title
    for team_norm in (home_norm, away_norm):
        words = [w for w in team_norm.split() if len(w) > 2]
        if any(w in title_norm for w in words):
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

        # Deduplicate: one entry per event_key, preserving all team/consensus data
        seen: dict[str, dict] = {}
        for r in fresh:
            if r.event_key not in seen:
                seen[r.event_key] = {
                    "event_key":      r.event_key,
                    "home_team":      r.outcome or "",        # outcome = home team
                    "away_team":      r.away_team or "",      # stored since migration 004
                    "consensus_home": r.consensus_prob or 0.5,
                    "consensus_away": r.consensus_away or (1 - (r.consensus_prob or 0.5)),
                    "bookmaker_count": 0,
                    "bookmakers":     [],
                    "min_home_prob":  r.consensus_prob or 0.5,
                    "max_home_prob":  r.consensus_prob or 0.5,
                }
            seen[r.event_key]["bookmaker_count"] += 1

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

        Returns a dict with:
          - consensus_prob      (probability for the traded side)
          - consensus_home_prob
          - consensus_away_prob
          - bookmaker_count
          - bookmakers          (list of per-bookmaker probs)
          - min_prob / max_prob (range across bookmakers for the traded side)
          - movement_pct        (None if no prior reading)

        Returns None if no matching event is found.
        """
        title = market.get("title", "")
        ticker = market.get("ticker", "")
        logger.debug("match_market: checking %d events for [%s] '%s'", len(events), ticker, title)

        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            logger.debug("  candidate event: home='%s' away='%s'", home, away)

            if _teams_overlap(title, home, away):
                # consensus_home is stored under "consensus_home" in live events
                # and also reconstructed under that key from the cache (_load_cached)
                consensus_home = event.get("consensus_home", 0.5)
                consensus_away = event.get("consensus_away", round(1 - consensus_home, 4))

                if side == "yes":
                    consensus_prob = consensus_home
                    min_prob = event.get("min_home_prob", consensus_home)
                    max_prob = event.get("max_home_prob", consensus_home)
                else:
                    consensus_prob = consensus_away
                    min_prob = round(1 - event.get("max_home_prob", consensus_home), 4)
                    max_prob = round(1 - event.get("min_home_prob", consensus_home), 4)

                return {
                    "consensus_prob":      round(consensus_prob, 4),
                    "consensus_home_prob": round(consensus_home, 4),
                    "consensus_away_prob": round(consensus_away, 4),
                    "bookmaker_count":     event.get("bookmaker_count", 0),
                    "bookmakers":          event.get("bookmakers", []),
                    "min_prob":            round(min_prob, 4),
                    "max_prob":            round(max_prob, 4),
                    "home_team":           home,
                    "away_team":           away,
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
