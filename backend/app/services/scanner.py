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
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.schemas import ScanResult
from app.services.ai_service import ai_service
from app.services.kalshi_client import kalshi_client
from app.services.news_service import news_service
from app.services.odds_service import odds_service
from app.services.trading_service import trading_service, get_available_cash

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


def _rule_prefilter(market: dict) -> Optional[str]:
    """
    Returns a skip reason string if the market should be excluded before
    any Claude call, or None if it passes all filters.
    """
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

    # Clean title: remove "?" and common filler patterns like "Will X win?"
    clean = title.rstrip("?").strip()
    for filler in (" win the game", " win?", " winner", " Winner", " Will ", "Will "):
        clean = clean.replace(filler, " ").strip()
    clean = " ".join(clean.split())   # collapse whitespace

    if competition:
        return f"{clean} {competition} prediction"

    # Sport-appropriate fallback labels
    sport_label = {
        "MLS": "soccer",
        "NBA": "NBA",
        "NFL": "NFL",
        "Cricket": "cricket",
    }.get(sport, sport)

    return f"{clean} {sport_label} prediction"


# ── Scanner ────────────────────────────────────────────────────────────────────

class MarketScanner:

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
        # Keep the highest-volume market per event_ticker to avoid correlated bets
        # on the same game (e.g. game winner + first half + totals all for Lakers).
        seen_events: dict[str, dict] = {}   # event_ticker -> best market dict
        event_sport: dict[str, str] = {}
        for market, sport in shortlisted:
            ev = market.get("event_ticker") or market.get("ticker", "")
            vol = _parse_volume(market)
            if ev not in seen_events or vol > _parse_volume(seen_events[ev]):
                seen_events[ev] = market
                event_sport[ev] = sport

        deduped = [(m, event_sport[ev]) for ev, m in seen_events.items()]
        if len(deduped) < len(shortlisted):
            logger.info("Deduplication: %d -> %d markets (one per event)",
                        len(shortlisted), len(deduped))

        # ── Steps 4 & 5: Haiku filter -> Sonnet decision ──────────────────
        async with AsyncSessionLocal() as db:
            # Compute available cash ONCE before the loop, then maintain it as
            # a running total in memory.  This prevents race conditions where two
            # trades in the same cycle both read the same (not-yet-updated) balance
            # and each believes it has the full available cash.
            available_cash = await get_available_cash(db)
            logger.info(
                "Scan starting with available cash: $%.2f (will track in-memory)",
                available_cash,
            )

            # Fetch sportsbook odds once per sport (cached for 6h)
            sport_odds: dict[str, list[dict]] = {}
            for sport in by_sport:
                try:
                    sport_odds[sport] = await odds_service.fetch_and_cache(db, sport)
                except Exception as exc:
                    logger.warning("OddsService fetch failed for %s: %s", sport, exc)
                    sport_odds[sport] = []

            for market, sport in deduped:
                markets_scanned += 1
                try:
                    trade = await self._process_market(
                        db, market, sport, available_cash=available_cash,
                        events=sport_odds.get(sport, []),
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
    ):
        title = market.get("title", market.get("ticker", ""))
        market_type = kalshi_client.get_market_type(market)
        headlines = await news_service.fetch_articles(_news_query(market, sport, title))

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

        # ── Stage 2: Sonnet full decision ──────────────────────────────────
        rule_signal = ai_service.compute_rule_signal(market)
        decision = await ai_service.decide(
            market, sport, sentiment, headlines, market_type,
            odds_context=odds_context,
        )
        logger.info(
            "Sonnet [%s] %s — trade=%s side=%s confidence=%.2f | %s",
            sport, market.get("ticker", "?"),
            decision.trade, decision.side, decision.confidence,
            decision.reasoning[:120],
        )

        # ── Persist signal (with odds data if available) ───────────────────
        consensus_prob  = odds_context["consensus_prob"]  if odds_context else None
        bookmaker_count = odds_context["bookmaker_count"] if odds_context else None
        line_movement   = odds_context.get("movement")   if odds_context else None
        await trading_service.save_signal(
            db=db, market_id=market.get("ticker", "UNKNOWN"),
            sport=sport, sentiment=sentiment, rule_signal=rule_signal,
            ai_recommendation=decision.reasoning,
            consensus_prob=consensus_prob,
            bookmaker_count=bookmaker_count,
            line_movement=line_movement,
        )

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


scanner = MarketScanner()
