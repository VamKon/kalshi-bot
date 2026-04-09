"""
Market scanner — orchestrates the full scan pipeline.

Cost-optimisation flow:
  1. Rule-based pre-filter  — no AI calls, eliminates near-certainties,
                               stale games, illiquid markets, games too close.
  2. Top-N by volume        — cap at MAX_MARKETS_PER_SCAN per sport.
  3. Event deduplication    — one trade per game/event (highest volume wins).
  4. Haiku quick-filter     — cheap sentiment + "interesting?" check (~80% of AI calls).
  5. Sonnet full decision   — only for markets that passed Haiku (~20% of AI calls).

Auto-resolution runs at the END of every scan via the shared run_resolve()
function from the resolve route, keeping resolution logic in one place.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.db_models import Trade, MarketSignal
from app.models.schemas import ScanResult
from app.services.ai_service import ai_service
from app.services.article_fetcher import article_fetcher
from app.services.cricket_extractor import cricket_extractor, cricket_facts_cache
from app.services.kalshi_client import kalshi_client
from app.services.news_service import news_service
from app.services.odds_service import odds_service
from app.services.sport_config import (
    ALLOWED_DOMESTIC_CRICKET_LEAGUES,
    ALLOWED_INTERNATIONAL_CRICKET_TEAMS,
    BLOCKED_COMPETITIONS,
)
from app.services.trading_service import trading_service, get_available_cash, sync_balance_from_kalshi

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_prob(market: dict) -> float:
    """Return the YES probability (0-1) from the market dict."""
    return float(market.get("yes_ask_dollars") or 0.5)


def _parse_volume(market: dict) -> float:
    return float(market.get("open_interest_fp") or market.get("volume") or 0)


def _parse_spread(market: dict) -> float:
    """Bid-ask spread as a fraction (0-1). Higher = more illiquid."""
    try:
        bid = float(market.get("yes_bid_dollars") or 0)
        ask = float(market.get("yes_ask_dollars") or 0)
        if ask > 0:
            return round(ask - bid, 4)
    except (TypeError, ValueError):
        pass
    return 1.0   # treat unparseable as maximally illiquid


def _hours_until_close(market: dict) -> Optional[float]:
    """Return hours until game resolution, or None if unparseable.
    Prefer expected_expiration_time (when game resolves) over close_time
    (which can be weeks away as a backstop deadline).
    """
    close_str = market.get("expected_expiration_time") or market.get("close_time")
    if not close_str:
        return None
    try:
        close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (close_dt - now).total_seconds() / 3600
    except Exception:
        return None


def _is_blocked_competition(market: dict) -> Optional[str]:
    """
    Return a reason string if this market should be skipped, otherwise None.

    Cricket uses two allowlists (not a blocklist):

    1. International series (KXT20MATCH, KXODI, KXTEST):
       BOTH teams must appear in ALLOWED_INTERNATIONAL_CRICKET_TEAMS.
       Any match involving a nation not in that set is skipped.

    2. Domestic series (KXIPL, KXPSL, KXCPL, KXSA20, KXVITBLAST, KXHUNDRED):
       The competition name must match ALLOWED_DOMESTIC_CRICKET_LEAGUES.
       Any domestic league not in the allowlist is skipped.

    Non-cricket markets use the BLOCKED_COMPETITIONS blocklist as before.
    """
    competition = (market.get("product_metadata") or {}).get("competition", "")
    title       = market.get("title", "") or market.get("subtitle", "")
    haystack    = f"{competition} {title}".lower()
    ticker      = (market.get("series_ticker") or market.get("ticker") or "").upper()

    # ── General blocklist (soccer, etc.) ──────────────────────────────────────
    for blocked in BLOCKED_COMPETITIONS:
        if blocked in haystack:
            return f"blocked competition '{blocked}' in '{competition or title}'"

    # ── Cricket international series ──────────────────────────────────────────
    # Approved nations (both teams in ALLOWED_INTERNATIONAL_CRICKET_TEAMS) are
    # traded at the normal MIN_CONFIDENCE threshold.
    # Unapproved T20 internationals (KXT20MATCH only) are allowed through but
    # held to a stricter 0.60 confidence threshold applied in _analyze_market.
    # ODI / Test unapproved matchups are still blocked (less Kalshi volume).
    INTL_PREFIXES = ("KXT20MATCH", "KXODI", "KXTEST", "KXCRIC")
    if any(ticker.startswith(p) for p in INTL_PREFIXES):
        matched = [t for t in ALLOWED_INTERNATIONAL_CRICKET_TEAMS if t in haystack]
        if len(matched) < 2:
            # Allow unapproved T20 internationals through — stricter threshold
            # enforced later in _analyze_market.  Block ODI / Test unapproved.
            if ticker.startswith("KXT20MATCH"):
                return None  # unapproved T20I — allow, higher threshold later
            return (
                f"international cricket — fewer than 2 allowed nations found "
                f"(found: {matched or 'none'}) in '{title}'"
            )
        return None  # both teams recognised — allow it

    # ── Cricket domestic series — competition must be in the allowlist ─────────
    # These prefixes match the *GAME series only (e.g. KXIPLGAME-...).
    DOMESTIC_PREFIXES = ("KXIPLGAME", "KXPSLGAME", "KXCPLGAME", "KXSA20GAME", "KXVITBLASTGAME", "KXHUNDREDGAME", "KXBBLGAME")
    if any(ticker.startswith(p) for p in DOMESTIC_PREFIXES):
        if any(league in haystack for league in ALLOWED_DOMESTIC_CRICKET_LEAGUES):
            return None  # recognised league — allow it
        # Fallback: if no competition metadata, trust the series prefix
        if not competition:
            return None
        return (
            f"domestic cricket league '{competition}' not in allowed leagues list"
        )

    return None


def _rule_prefilter(market: dict) -> Optional[str]:
    """
    Returns a skip reason string if the market should be excluded before
    any Claude call, or None if it passes all filters.
    """
    # Skip blocked competitions (international friendlies, obscure leagues, etc.)
    blocked = _is_blocked_competition(market)
    if blocked:
        return blocked

    # Skip non-game-winner markets when the filter is enabled
    if settings.GAME_WINNER_ONLY:
        market_type = kalshi_client.get_market_type(market)
        if market_type != "game_winner":
            return f"market type '{market_type}' — game_winner_only is enabled"

    prob = _parse_prob(market)

    # Skip near-certainties (< 10% or > 90%)
    if prob < settings.MARKET_PROB_MIN or prob > settings.MARKET_PROB_MAX:
        return f"probability {prob:.2f} outside [{settings.MARKET_PROB_MIN}, {settings.MARKET_PROB_MAX}]"

    # Skip games outside the time window
    hours = _hours_until_close(market)
    if hours is None:
        return "close_time unparseable"
    if hours > settings.MARKET_HOURS_AHEAD:
        return f"closes in {hours:.0f}h — outside {settings.MARKET_HOURS_AHEAD}h window"
    if hours < settings.MARKET_MIN_HOURS_AHEAD:
        return f"closes in {hours:.1f}h — game too imminent (<{settings.MARKET_MIN_HOURS_AHEAD}h)"

    # Skip markets with insufficient volume / open interest
    vol = _parse_volume(market)
    if vol < settings.MIN_MARKET_VOLUME:
        return f"volume {vol:.0f} < minimum {settings.MIN_MARKET_VOLUME}"

    # Skip illiquid markets with wide bid-ask spread
    spread = _parse_spread(market)
    if spread > settings.MAX_BID_ASK_SPREAD:
        return f"bid-ask spread {spread:.3f} > max {settings.MAX_BID_ASK_SPREAD}"

    return None


# ── News query builder ─────────────────────────────────────────────────────────

def _clean_title(title: str) -> str:
    """Strip trailing '?' and common filler words from a market title."""
    clean = title.rstrip("?").strip()
    for filler in (" win the game", " win?", " winner", " Winner", " Will ", "Will "):
        clean = clean.replace(filler, " ").strip()
    return " ".join(clean.split())


def _parse_teams_from_title(title: str) -> tuple[str, str]:
    """
    Extract home/away team names from a market title like 'India vs Pakistan'.
    Returns ("", "") if the separator is not found.
    Handles both 'vs' and 'v' separators, case-insensitive.
    """
    # Strip common suffixes like '?' and trailing crud
    clean = title.rstrip("?").strip()
    # Remove known filler phrases that appear after team names
    for filler in (" win the game", " winner", " Winner"):
        clean = clean.replace(filler, "").strip()

    for sep in (" vs ", " v ", " VS ", " V "):
        if sep in clean:
            parts = clean.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    return "", ""


def _news_query(market: dict, sport: str, title: str) -> str:
    """
    Build a targeted news search query for a market.

    Problems we avoid:
    - "Winner?" in the market title confuses search engines (returns Q&A pages)
    - Appending "MLS" for European soccer leagues returns wrong results
    - Generic titles return generic (often outdated) articles

    Strategy:
    1. Strip trailing "?" and filler words from the title.
    2. Use the competition name from product_metadata if available (e.g. "Premier League").
    3. Fall back to sport-appropriate search terms.
    """
    # Use product_metadata competition if present — most accurate
    competition = (market.get("product_metadata") or {}).get("competition", "")
    clean = _clean_title(title)

    if competition:
        return f"{clean} {competition} prediction"

    sport_label = {
        "MLS": "soccer",
        "NBA": "NBA",
        "NFL": "NFL",
        "Cricket": "cricket",
    }.get(sport, sport)

    return f"{clean} {sport_label} prediction"


def _news_queries(market: dict, sport: str, title: str) -> list[str]:
    """
    Return a list of targeted queries for a market.

    For cricket we fetch three separate queries in parallel:
      1. General match preview / prediction (existing logic)
      2. Toss result — available ~30 min before game, hugely predictive in T20
      3. Playing XI / squad / injury news — who's actually playing today

    For other sports a single general query is returned.
    Each query is cached independently in NewsService, so parallel fetches
    within the same scan window reuse cached results cheaply.
    """
    base = _news_query(market, sport, title)
    if sport != "Cricket":
        return [base]

    clean = _clean_title(title)
    return [
        base,
        f"{clean} toss result today",
        f"{clean} playing XI squad injury 2026",
    ]


# ── Scanner ────────────────────────────────────────────────────────────────────

class MarketScanner:
    """
    _in_flight: tickers currently being evaluated by ANY concurrent scan
    (regular or toss-triggered).  Checked synchronously before each
    _process_market call — since asyncio is single-threaded, the check+add
    is atomic with respect to other coroutines, preventing double-evaluation
    of the same market when a scheduled scan and a toss mini-scan overlap.
    """

    def __init__(self) -> None:
        self._in_flight: set[str] = set()

    async def run(self) -> ScanResult:
        # Import here to avoid circular imports (resolve imports trading_service,
        # scanner imports ai_service — keeping the dependency arrow one-way).
        from app.api.routes.resolve import run_resolve

        logger.info("Starting market scan ...")
        markets_scanned = 0
        trades_placed = 0

        all_markets = await kalshi_client.get_markets(limit=1000)

        # ── Step 1: classify by sport ──────────────────────────────────────
        by_sport: dict[str, list[dict]] = {}
        for market in all_markets:
            sport = kalshi_client.classify_sport(market)
            if sport:
                by_sport.setdefault(sport, []).append(market)

        # Filter to only the sports the user has enabled in Settings
        enabled_sports = set(settings.MONITORED_SPORTS)
        skipped_sports = [s for s in by_sport if s not in enabled_sports]
        if skipped_sports:
            logger.info("Sports filter: skipping %s (not in monitored_sports=%s)",
                        skipped_sports, list(enabled_sports))
        by_sport = {s: v for s, v in by_sport.items() if s in enabled_sports}

        # ── Step 2: rule-based pre-filter + top-N per sport by volume ──────
        shortlisted: list[tuple[dict, str]] = []
        for sport, candidates in by_sport.items():
            passed = []
            for m in candidates:
                reason = _rule_prefilter(m)
                if reason:
                    logger.info("Pre-filter skip [%s] %s: %s",
                                sport, m.get("ticker", "?"), reason)
                else:
                    passed.append(m)

            # Sort by volume descending, take top MAX_MARKETS_PER_SCAN
            passed.sort(key=_parse_volume, reverse=True)
            top = passed[:settings.MAX_MARKETS_PER_SCAN]
            logger.info("Sport %s: %d candidates -> %d after pre-filter (top %d)",
                        sport, len(candidates), len(passed), len(top))
            shortlisted.extend((m, sport) for m in top)

        # ── Step 3: deduplicate — one market per event ─────────────────────
        # Pass A: highest-volume market per event_ticker
        # Catches multiple market types (winner / first-half / totals) for the
        # same Kalshi event.
        seen_events: dict[str, dict] = {}   # event_ticker -> best market dict
        event_sport: dict[str, str] = {}
        for market, sport in shortlisted:
            ev = market.get("event_ticker") or market.get("ticker", "")
            vol = _parse_volume(market)
            if ev not in seen_events or vol > _parse_volume(seen_events[ev]):
                seen_events[ev] = market
                event_sport[ev] = sport

        after_pass_a = [(m, event_sport[ev]) for ev, m in seen_events.items()]

        # Pass B: highest-volume market per *normalised title*
        # Catches the same underlying game listed under two different Kalshi
        # series tickers (e.g. a standalone game market AND a parlay-wrapper
        # market for the same LSG vs DC fixture).  We normalise by lowercasing
        # and removing all non-alpha characters so "LSG vs DC" and "Lsg Vs Dc"
        # collapse to the same key.
        def _title_key(m: dict) -> str:
            raw = m.get("title") or m.get("subtitle") or ""
            return re.sub(r"[^a-z0-9]", "", _clean_title(raw).lower())

        seen_titles: dict[str, dict] = {}
        title_sport: dict[str, str] = {}
        for market, sport in after_pass_a:
            key = _title_key(market)
            vol = _parse_volume(market)
            if not key:  # untitled market — keep it
                seen_titles[market.get("ticker", "")] = market
                title_sport[market.get("ticker", "")] = sport
            elif key not in seen_titles or vol > _parse_volume(seen_titles[key]):
                seen_titles[key] = market
                title_sport[key] = sport

        deduped = [(m, title_sport[k]) for k, m in seen_titles.items()]
        if len(deduped) < len(shortlisted):
            logger.info(
                "Deduplication: %d -> %d markets (pass A: event_ticker, pass B: title)",
                len(shortlisted), len(deduped),
            )

        # ── Steps 4 & 5: Haiku filter -> Sonnet decision ──────────────────
        async with AsyncSessionLocal() as db:
            # Sync DB balance from Kalshi before sizing any trades — this
            # ensures Kelly uses the actual account balance, not a stale DB
            # value that may be wrong after a deposit or withdrawal.
            synced = await sync_balance_from_kalshi(db)
            if synced is not None:
                logger.info("Kalshi balance synced: $%.2f", synced)

            # Compute available cash ONCE before the loop, then maintain it as
            # a running total in memory.  This prevents race conditions where two
            # trades in the same cycle both read the same (not-yet-updated) balance
            # and each believes it has the full available cash.
            available_cash = await get_available_cash(db)
            logger.info(
                "Scan starting with available cash: $%.2f (will track in-memory)",
                available_cash,
            )

            # Load all open trade market IDs so we can skip markets where we
            # already hold a position — prevents betting both sides of the same game.
            result = await db.execute(
                select(Trade.market_id).where(Trade.status == "open")
            )
            open_market_ids: set[str] = {row[0] for row in result.fetchall()}
            if open_market_ids:
                logger.info(
                    "Open positions: %d markets — will skip if re-encountered: %s",
                    len(open_market_ids), open_market_ids,
                )

            # Bulk-load the most recent yes_ask per market from market_signals
            # so we can compute price movement without an extra DB query per market.
            prev_yes_ask_map: dict[str, tuple[float, float]] = {}  # ticker → (yes_ask, hours_ago)
            try:
                from datetime import datetime, timezone as _tz
                from sqlalchemy import desc as _desc
                sig_result = await db.execute(
                    select(MarketSignal.market_id, MarketSignal.yes_ask, MarketSignal.scanned_at)
                    .where(MarketSignal.yes_ask.isnot(None))
                    .order_by(_desc(MarketSignal.scanned_at))
                    .limit(500)
                )
                seen_tickers: set[str] = set()
                now_utc = datetime.now(_tz.utc)
                for row in sig_result.fetchall():
                    mid, ya, scanned_at = row
                    if mid not in seen_tickers and ya is not None:
                        seen_tickers.add(mid)
                        scanned_at_aware = (
                            scanned_at.replace(tzinfo=_tz.utc)
                            if scanned_at.tzinfo is None else scanned_at
                        )
                        hours_ago = (now_utc - scanned_at_aware).total_seconds() / 3600
                        prev_yes_ask_map[mid] = (float(ya), round(hours_ago, 1))
                if prev_yes_ask_map:
                    logger.info(
                        "Price movement tracking: loaded previous yes_ask for %d markets",
                        len(prev_yes_ask_map),
                    )
            except Exception as exc:
                logger.warning("Could not load previous yes_ask values: %s", exc)

            # Fetch sportsbook odds once per sport (cached for 6h)
            sport_odds: dict[str, list[dict]] = {}
            for sport in by_sport:
                try:
                    sport_odds[sport] = await odds_service.fetch_and_cache(db, sport)
                except Exception as exc:
                    logger.warning("OddsService fetch failed for %s: %s", sport, exc)
                    sport_odds[sport] = []

            for market, sport in deduped:
                ticker = market.get("ticker", "")
                if ticker in open_market_ids:
                    logger.info(
                        "Skipping %s — open position already exists on this market",
                        ticker,
                    )
                    continue
                markets_scanned += 1
                prev_ask_entry = prev_yes_ask_map.get(ticker)
                prev_ask   = prev_ask_entry[0] if prev_ask_entry else None
                hours_ago  = prev_ask_entry[1] if prev_ask_entry else 2.0

                # In-flight guard: skip if a toss mini-scan is already
                # evaluating this ticker concurrently.
                if ticker in self._in_flight:
                    logger.info(
                        "Skipping %s — already being evaluated by a concurrent scan",
                        ticker,
                    )
                    continue
                self._in_flight.add(ticker)
                try:
                    trade = await self._process_market(
                        db, market, sport, available_cash=available_cash,
                        events=sport_odds.get(sport, []),
                        prev_yes_ask=prev_ask,
                        prev_scan_hours_ago=hours_ago,
                    )
                    if trade:
                        trades_placed += 1
                        # Deduct from the in-memory running total so the next
                        # iteration uses the correct reduced budget.
                        available_cash = max(0.0, available_cash - trade.stake)
                        logger.info(
                            "Available cash after trade: $%.2f", available_cash
                        )
                except Exception as exc:
                    logger.error("Error processing market %s: %s",
                                 market.get("ticker", "?"), exc)
                finally:
                    self._in_flight.discard(ticker)

        # ── Step 6: resolve any completed trades ──────────────────────────
        resolve_result = await run_resolve()
        if resolve_result.trades_resolved:
            logger.info(
                "Post-scan resolution: %d resolved (%d wins / %d losses)",
                resolve_result.trades_resolved,
                resolve_result.wins,
                resolve_result.losses,
            )

        result = ScanResult(markets_scanned=markets_scanned,
                            trades_placed=trades_placed,
                            timestamp=datetime.utcnow())
        logger.info("Scan complete — %d markets scanned, %d trades placed",
                    markets_scanned, trades_placed)
        return result

    async def _process_market(
        self,
        db,
        market: dict,
        sport: str,
        available_cash: Optional[float] = None,
        events: Optional[list[dict]] = None,
        prev_yes_ask: Optional[float] = None,
        prev_scan_hours_ago: float = 2.0,
        extra_headlines: Optional[list[str]] = None,
    ):
        title = market.get("title", market.get("ticker", ""))
        market_type = kalshi_client.get_market_type(market)

        # Fetch multiple targeted queries in parallel (each cached independently).
        # Passing sport="Cricket" triggers the ESPNcricinfo RSS merge in NewsService.
        queries = _news_queries(market, sport, title)
        headline_lists = await asyncio.gather(
            *[news_service.fetch_articles(q, sport=sport) for q in queries]
        )
        # Flatten and deduplicate on first 60 chars to avoid near-duplicate headlines.
        # extra_headlines (e.g. toss result) are prepended so they appear first.
        seen: set[str] = set()
        headlines: list[str] = list(extra_headlines or [])
        for h in headlines:
            seen.add(h[:60])
        for hl in headline_lists:
            for h in hl:
                key = h[:60]
                if key not in seen:
                    seen.add(key)
                    headlines.append(h)

        # ── Stage 1: Haiku quick-filter ────────────────────────────────────
        interesting, sentiment, skip_reason = await ai_service.quick_filter(
            market, sport, headlines, market_type
        )
        if not interesting:
            yes_ask  = float(market.get("yes_ask_dollars") or 0)
            yes_bid  = float(market.get("yes_bid_dollars") or 0)
            spread   = round(yes_ask - yes_bid, 4)
            hours    = _hours_until_close(market) or 0
            logger.info(
                "Haiku skipped [%s] %s — %s (YES=%.2f spread=%.3f %.1fh to game)",
                sport, market.get("ticker", "?"),
                skip_reason or "no edge identified",
                yes_ask, spread, hours,
            )
            return None

        # ── Sportsbook odds match ───────────────────────────────────────────
        # Try to find a matching Odds API event for richer edge calculation.
        odds_context: Optional[dict] = None
        if events:
            yes_bid_tmp, yes_ask_tmp = kalshi_client.extract_best_price(market)
            # Determine the likely traded side based on Sonnet's eventual decision;
            # for now use "yes" as default — the matched event is the same regardless.
            matched = odds_service.match_market(market, events, side="yes")
            if matched:
                matched["movement"] = odds_service.describe_movement(
                    matched["consensus_prob"], None
                )
                odds_context = matched
                logger.info(
                    "OddsService match [%s] %s — consensus_prob=%.3f (%s vs %s)",
                    sport, market.get("ticker", "?"),
                    matched["consensus_prob"],
                    matched.get("home_team", "?"),
                    matched.get("away_team", "?"),
                )

        # ── Sportsbook coverage gate ────────────────────────────────────────
        if odds_context is None and settings.REQUIRE_SPORTSBOOK_ODDS:
            logger.info(
                "Skipping %s — no sportsbook odds found and REQUIRE_SPORTSBOOK_ODDS=True",
                market.get("ticker", "?"),
            )
            return None

        # ── Stage 2: Fetch cricket facts (OpenRouter extraction) ───────────────
        # Runs for ALL cricket markets — does NOT require a sportsbook odds match.
        # When odds_context is available we use its richer team/event metadata;
        # otherwise we fall back to parsing team names from the market title so
        # OpenRouter extraction still fires even when The Odds API has no match.
        # Checks DB cache first; falls back to live article fetch + extraction.
        # Gracefully skips when OPENROUTER_API_KEY is not set.
        cricket_facts = None
        # articles is hoisted here so it can be passed to ai_service.decide() for
        # raw-text injection into the Sonnet prompt, even when OpenRouter extraction
        # succeeds (providing both structured facts AND article excerpts to Claude).
        articles: list[dict] = []
        if sport == "Cricket":
            if odds_context:
                home_team    = odds_context.get("home_team", "")
                away_team    = odds_context.get("away_team", "")
                event_key    = odds_context.get("event_key", "") or market.get("ticker", "")
                competition  = odds_context.get("competition", "")
                match_format = odds_context.get("match_format", "T20")
                commence_dt_str = odds_context.get("commence_time", "")
            else:
                # No sportsbook match — parse teams from the market title
                home_team, away_team = _parse_teams_from_title(title)
                event_key    = market.get("event_ticker") or market.get("ticker", "")
                competition  = (market.get("product_metadata") or {}).get("competition", "")
                match_format = "T20"   # safe default for KXT20MATCH series
                commence_dt_str = market.get("expected_expiration_time") or market.get("close_time") or ""

            if home_team and away_team and event_key:
                cricket_facts = await cricket_facts_cache.get(db, event_key)
                if cricket_facts is None:
                    logger.info(
                        "CricketFacts cache miss for %s — fetching articles (%s vs %s)%s",
                        event_key, home_team, away_team,
                        " [no sportsbook match]" if not odds_context else "",
                    )
                    articles = await article_fetcher.fetch_match_articles(
                        home_team, away_team, competition, max_articles=10
                    )
                    if articles:
                        cricket_facts = await cricket_extractor.extract_from_multiple(
                            articles, home_team, away_team, match_format, competition
                        )
                        # Parse commence_time for TTL calculation
                        try:
                            from datetime import datetime as _dt
                            commence_dt = _dt.fromisoformat(
                                commence_dt_str.replace("Z", "+00:00")
                            ) if commence_dt_str else None
                        except Exception:
                            commence_dt = None
                        await cricket_facts_cache.set(db, event_key, cricket_facts, commence_dt)
                    else:
                        logger.info("No articles found for %s vs %s — proceeding without facts", home_team, away_team)
                else:
                    logger.info(
                        "CricketFacts cache hit for %s (confidence=%.2f, toss=%s)",
                        event_key, cricket_facts.extraction_confidence, cricket_facts.toss_winner,
                    )
            else:
                logger.info(
                    "CricketFacts skipped for %s — could not determine team names from title '%s'",
                    market.get("ticker", "?"), title,
                )

        # ── Stage 3: Sonnet full decision ──────────────────────────────────
        # Extract venue from odds_context (populated by match_market via VENUE_MAP).
        # None when no Odds API match was found — AI prompt simply omits the block.
        venue = odds_context.get("venue") if odds_context else None

        rule_signal = ai_service.compute_rule_signal(market)
        decision = await ai_service.decide(
            market, sport, sentiment, headlines, market_type,
            odds_context=odds_context,
            prev_yes_ask=prev_yes_ask,
            prev_scan_hours_ago=prev_scan_hours_ago,
            venue=venue,
            cricket_facts=cricket_facts,
            articles=articles,   # freshly fetched article bodies (empty on cache hit)
        )
        logger.info(
            "Sonnet [%s] %s — trade=%s side=%s confidence=%.2f | %s",
            sport, market.get("ticker", "?"),
            decision.trade, decision.side, decision.confidence,
            decision.reasoning[:120],
        )

        # ── Correct odds_context for the actual trade side ─────────────────
        # match_market was called with side="yes" before Sonnet ran.  If Sonnet
        # decided to trade NO, consensus_prob in odds_context still reflects the
        # YES side — we need to flip it so edge and Kelly use the right number.
        if odds_context and decision.trade and decision.side == "no":
            yes_is_home = odds_context.get("yes_is_home", True)
            if yes_is_home:
                # YES=home → NO=away
                no_consensus = odds_context["consensus_away_prob"]
            else:
                # YES=away → NO=home
                no_consensus = odds_context["consensus_home_prob"]
            # min/max were computed for the YES side; invert them for NO.
            no_min = round(1 - odds_context.get("max_prob", 1 - no_consensus), 4)
            no_max = round(1 - odds_context.get("min_prob", 1 - no_consensus), 4)
            odds_context = {
                **odds_context,
                "consensus_prob": round(no_consensus, 4),
                "min_prob":       no_min,
                "max_prob":       no_max,
            }
            logger.info(
                "odds_context flipped to NO side: consensus_prob=%.3f (yes_is_home=%s)",
                no_consensus, yes_is_home,
            )

        # ── Persist signal (with odds data if available) ───────────────────
        # Always store the YES-side consensus so the UI edge formula
        # (consensus_prob - yes_ask) stays correct regardless of which side
        # the AI traded.  The flip above is only needed for Kelly sizing.
        if odds_context:
            yes_is_home = odds_context.get("yes_is_home", True)
            if yes_is_home:
                consensus_prob_yes = odds_context.get("consensus_home_prob")
            else:
                consensus_prob_yes = odds_context.get("consensus_away_prob")
            consensus_prob  = consensus_prob_yes
            bookmaker_count = odds_context["bookmaker_count"]
            line_movement   = odds_context.get("movement")
        else:
            consensus_prob  = None
            bookmaker_count = None
            line_movement   = None
        await trading_service.save_signal(
            db=db, market_id=market.get("ticker", "UNKNOWN"),
            sport=sport, sentiment=sentiment, rule_signal=rule_signal,
            ai_recommendation=decision.reasoning,
            consensus_prob=consensus_prob,
            bookmaker_count=bookmaker_count,
            line_movement=line_movement,
            yes_ask=float(market.get("yes_ask_dollars") or 0),
            venue=venue,
        )

        # ── Stricter threshold for unapproved T20 internationals ──────────────
        # KXT20MATCH markets where neither/only one team is in
        # ALLOWED_INTERNATIONAL_CRICKET_TEAMS are allowed through the pre-filter
        # but require confidence >= 0.60 (vs the normal MIN_CONFIDENCE of 0.50).
        ticker = (market.get("series_ticker") or market.get("ticker") or "").upper()
        if ticker.startswith("KXT20MATCH"):
            haystack = f"{market.get('title', '')} {(market.get('product_metadata') or {}).get('competition', '')}".lower()
            matched_nations = [t for t in ALLOWED_INTERNATIONAL_CRICKET_TEAMS if t in haystack]
            if len(matched_nations) < 2:
                UNAPPROVED_T20I_MIN_CONFIDENCE = 0.60
                if decision.confidence < UNAPPROVED_T20I_MIN_CONFIDENCE:
                    logger.info(
                        "Skipping unapproved T20I %s — confidence %.2f < %.2f",
                        market.get("ticker", "?"), decision.confidence, UNAPPROVED_T20I_MIN_CONFIDENCE,
                    )
                    return None

        yes_bid, yes_ask = kalshi_client.extract_best_price(market)

        if decision.side == "yes":
            entry_price = yes_ask          # cost to buy YES
            market_prob = yes_ask          # market-implied prob of YES
        else:
            entry_price = 1.0 - yes_bid   # cost to buy NO
            market_prob = 1.0 - yes_ask   # market-implied prob of NO

        return await trading_service.execute_paper_trade(
            db=db, market=market, sport=sport,
            decision=decision, entry_price=entry_price, market_prob=market_prob,
            available_cash=available_cash,
            odds_context=odds_context,
        )


    async def run_toss_triggered(
        self,
        markets: list[dict],
        toss_headline: str = "",
    ) -> None:
        """
        Targeted mini-scan fired by TossWatcher when a toss result is detected.

        Key differences from run():
        - Only processes the specific markets passed in (already matched to toss).
        - Relaxes MARKET_MIN_HOURS_AHEAD to 10 minutes — games are typically
          20–30 min away when the toss drops, but we still have time to trade.
        - Injects the toss headline at the front of every market's news list so
          Sonnet sees it prominently regardless of Google News / ESPNcricinfo results.
        - Does NOT run a full resolve cycle at the end (leave that to the main scan).
        """
        logger.info(
            "TossWatcher mini-scan: %d market(s) | toss='%s'",
            len(markets), toss_headline[:80],
        )

        async with AsyncSessionLocal() as db:
            available_cash = await get_available_cash(db)

            result = await db.execute(
                select(Trade.market_id).where(Trade.status == "open")
            )
            open_market_ids: set[str] = {row[0] for row in result.fetchall()}

            for market in markets:
                ticker = market.get("ticker", "")
                if ticker in open_market_ids:
                    logger.info(
                        "TossWatcher: skipping %s — open position exists", ticker
                    )
                    continue

                # Relax min-hours gate: accept games as close as 10 min away
                hours = _hours_until_close(market) or 0
                if hours < (10 / 60):
                    logger.info(
                        "TossWatcher: skipping %s — game in %.0fm, too imminent",
                        ticker, hours * 60,
                    )
                    continue
                if hours > settings.MARKET_HOURS_AHEAD:
                    logger.info(
                        "TossWatcher: skipping %s — game in %.1fh, outside window",
                        ticker, hours,
                    )
                    continue

                sport = "Cricket"

                # In-flight guard: skip if the regular 2h scan is already
                # evaluating this ticker concurrently.
                if ticker in self._in_flight:
                    logger.info(
                        "TossWatcher: skipping %s — already being evaluated by "
                        "a concurrent regular scan",
                        ticker,
                    )
                    continue
                self._in_flight.add(ticker)
                try:
                    # Prepend the toss headline so the AI sees it first
                    extra_headlines = [f"TOSS RESULT: {toss_headline}"] if toss_headline else []
                    trade = await self._process_market(
                        db, market, sport,
                        available_cash=available_cash,
                        events=[],
                        extra_headlines=extra_headlines,
                    )
                    if trade:
                        available_cash = max(0.0, available_cash - trade.stake)
                        logger.info(
                            "TossWatcher trade placed: %s  cash_remaining=%.2f",
                            ticker, available_cash,
                        )
                except Exception as exc:
                    logger.error(
                        "TossWatcher error processing %s: %s", ticker, exc
                    )
                finally:
                    self._in_flight.discard(ticker)


scanner = MarketScanner()
