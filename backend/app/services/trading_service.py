"""
Core trading logic: Kelly Criterion sizing, paper/live trade execution, resolution.

Paper trading (PAPER_TRADING=True, default):
  Trades are recorded in the database only — no real orders sent to Kalshi.

Live trading (PAPER_TRADING=False):
  Trades are placed as real limit orders on Kalshi via the API.
  The order must fully or partially fill before the trade is recorded.
  Set KALSHI_API_BASE_URL=https://trading-api.kalshi.co/trade-api/v2 and
  provide production KALSHI_KEY_ID + KALSHI_PRIVATE_KEY credentials.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.db_models import Portfolio, Trade, MarketSignal
from app.models.schemas import AIDecision

logger = logging.getLogger(__name__)


def kelly_stake(available_cash: float, probability: float, odds: float,
                kelly_fraction: float = settings.KELLY_FRACTION) -> float:
    """
    Fractional Kelly stake sized against AVAILABLE CASH (not total balance).

    Uses available_cash so that already-deployed capital is excluded from sizing,
    preventing over-commitment when multiple open trades exist.

    Caps at MAX_TRADE_PCT of available_cash and MAX_TRADE_USD (hard dollar cap).
    Returns 0 if there is no positive edge or the raw stake is negligible.
    """
    if odds <= 0 or probability <= 0:
        return 0.0
    q = 1.0 - probability
    f_star = (odds * probability - q) / odds
    if f_star <= 0:
        return 0.0
    # Size against available cash only
    raw_stake = f_star * kelly_fraction * available_cash
    if raw_stake < 0.10:
        return 0.0
    stake = min(raw_stake,
                available_cash * settings.MAX_TRADE_PCT,
                settings.MAX_TRADE_USD)
    return round(stake, 2)


def compute_edge(probability: float, market_price: float) -> float:
    """
    Edge = estimated_probability - market_implied_probability.
    Positive edge means we think the true probability is higher than the market.
    """
    return round(probability - market_price, 4)


async def get_or_create_portfolio(db: AsyncSession) -> Portfolio:
    result = await db.execute(select(Portfolio).limit(1))
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(balance=settings.INITIAL_BANKROLL)
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)
    return portfolio


async def get_deployed_stake(db: AsyncSession) -> float:
    """Return the sum of stakes across all currently open trades."""
    result = await db.execute(
        select(func.coalesce(func.sum(Trade.stake), 0.0)).where(Trade.status == "open")
    )
    return float(result.scalar_one())


async def get_available_cash(db: AsyncSession) -> float:
    """
    True spendable cash = portfolio balance minus capital already deployed in open trades.

    In normal operation portfolio.balance already has open-trade stakes deducted,
    so available_cash ≈ portfolio.balance.  This function recomputes it from first
    principles so it stays correct even if the balance is manually adjusted or if
    there is ever any drift between the balance and the open trade stakes.
    """
    portfolio = await get_or_create_portfolio(db)
    deployed = await get_deployed_stake(db)
    return max(0.0, portfolio.balance - deployed)


async def sync_balance_from_kalshi(db: AsyncSession) -> Optional[float]:
    """
    Pull the live total portfolio value from Kalshi and update the DB record.

    We sync from `portfolio_value` (cash + open-position market value), NOT
    from `balance` (cash only).  The DB portfolio.balance represents the
    *total* funds — deployed stakes are then subtracted to get available cash.
    Syncing to cash-only would cause double-subtraction:
        available_cash = cash_only_balance − deployed  →  too low

    Only writes to DB when drift > $0.05 to avoid spurious commits from
    rounding noise on unsettled contracts.

    Returns the synced total value, or None if Kalshi was unreachable.
    """
    from app.services.kalshi_client import kalshi_client  # avoid circular import

    kalshi_data = await kalshi_client.get_balance()
    # Prefer portfolio_value (total); fall back to cash balance if unavailable
    live_total = kalshi_data.get("portfolio_value") or kalshi_data.get("balance")

    if live_total is None:
        logger.warning("sync_balance_from_kalshi: Kalshi balance unavailable, keeping DB value")
        return None

    portfolio = await get_or_create_portfolio(db)
    diff = abs(live_total - portfolio.balance)

    if diff > 0.05:
        logger.info(
            "Syncing balance from Kalshi: DB=$%.2f → Kalshi portfolio_value=$%.2f (diff=$%.2f)",
            portfolio.balance, live_total, diff,
        )
        portfolio.balance = round(live_total, 2)
        portfolio.updated_at = datetime.utcnow()
        await db.commit()
    else:
        logger.debug(
            "Balance in sync: DB=$%.2f, Kalshi=$%.2f (diff=$%.4f — no update needed)",
            portfolio.balance, live_total, diff,
        )

    return live_total


async def update_balance(db: AsyncSession, delta: float) -> None:
    portfolio = await get_or_create_portfolio(db)
    portfolio.balance = max(0.0, portfolio.balance + delta)
    portfolio.updated_at = datetime.utcnow()
    await db.commit()


class TradingService:

    async def execute_paper_trade(
        self,
        db: AsyncSession,
        market: dict,
        sport: str,
        decision: AIDecision,
        entry_price: float,
        market_prob: float,
        available_cash: Optional[float] = None,
        odds_context: Optional[dict] = None,
    ) -> Optional[Trade]:
        """
        Size a trade via Kelly Criterion and either simulate it (paper) or
        place a real limit order on Kalshi (live), then persist to the database.

        entry_price    — market-implied contract cost in [0, 1]
        market_prob    — market-implied probability of the traded outcome winning
        available_cash — caller-supplied cash budget; computed from DB if None
        odds_context   — optional dict from OddsService.match_market(); when present,
                         consensus_prob replaces AI confidence as the Kelly 'p'
                         estimate and edge is computed vs the sportsbook consensus.
        """
        if not decision.trade:
            return None
        if decision.confidence < settings.MIN_CONFIDENCE:
            logger.info("Skipping — confidence %.2f < threshold %.2f",
                        decision.confidence, settings.MIN_CONFIDENCE)
            return None

        # ── Edge calculation ────────────────────────────────────────────────
        # When sportsbook consensus is available, use it as the primary edge
        # signal and as Kelly's 'p'. AI confidence becomes a gate (≥ MIN_CONFIDENCE,
        # already checked above) and a modifier applied later to kelly_fraction.
        if odds_context and odds_context.get("consensus_prob") is not None:
            consensus_p = odds_context["consensus_prob"]
            edge = compute_edge(consensus_p, market_prob)
            kelly_p = consensus_p
            logger.info(
                "Using sportsbook consensus for edge: consensus_p=%.3f market_prob=%.3f edge=%.3f",
                consensus_p, market_prob, edge,
            )
        else:
            edge = compute_edge(decision.confidence, market_prob)
            kelly_p = decision.confidence

        if edge < settings.MIN_EDGE_THRESHOLD:
            logger.info("Skipping — edge %.3f < min threshold %.3f",
                        edge, settings.MIN_EDGE_THRESHOLD)
            return None

        if entry_price <= 0 or entry_price >= 1:
            logger.warning("Invalid entry price: %.3f — skipping", entry_price)
            return None

        # ── Resolve available cash ──────────────────────────────────────────
        if available_cash is None:
            available_cash = await get_available_cash(db)

        if available_cash < 1.0:
            logger.warning(
                "Insufficient available cash (%.2f) — skipping %s",
                available_cash, market.get("ticker", "?"),
            )
            return None

        # ── Kelly sizing against available cash ─────────────────────────────
        # When sportsbook consensus is available, scale Kelly fraction by how
        # closely AI confidence agrees with the consensus (both bullish → full
        # fraction; lukewarm AI agreement → conservative fraction).
        effective_kelly_fraction = settings.KELLY_FRACTION
        if odds_context and odds_context.get("consensus_prob") is not None:
            ai_confidence = decision.confidence
            consensus_p = odds_context["consensus_prob"]
            # Modifier: if AI and consensus agree directionally (both > 0.5 or
            # both < 0.5), use full fraction; if they diverge, reduce to 60%.
            if (ai_confidence > 0.5) == (consensus_p > 0.5):
                effective_kelly_fraction = settings.KELLY_FRACTION
            else:
                effective_kelly_fraction = settings.KELLY_FRACTION * 0.6

        odds  = (1.0 - market_prob) / market_prob
        stake = kelly_stake(available_cash, kelly_p, odds,
                            kelly_fraction=effective_kelly_fraction)

        if stake <= 0:
            logger.info("Kelly stake zero — no edge, skipping")
            return None

        if stake > available_cash:
            logger.warning(
                "Proposed stake %.2f exceeds available cash %.2f — skipping %s",
                stake, available_cash, market.get("ticker", "?"),
            )
            return None

        ticker   = market.get("ticker", "UNKNOWN")
        order_id = None   # populated for live trades

        # ── Live order placement ────────────────────────────────────────────
        if not settings.PAPER_TRADING:
            # Import here to avoid circular imports at module load time
            from app.services.kalshi_client import kalshi_client

            # Kalshi prices are integers in cents (1–99).
            # count = number of contracts; each costs entry_price dollars.
            limit_cents = max(1, min(99, round(entry_price * 100)))
            count       = max(1, round(stake / entry_price))

            order = await kalshi_client.place_order(
                ticker=ticker,
                side=decision.side,
                count=count,
                limit_price_cents=limit_cents,
            )

            if not order:
                logger.warning("Live order failed for %s — skipping", ticker)
                return None

            order_status = order.get("status", "")
            filled = order.get("count", 0) - order.get("remaining_count", order.get("count", 0))

            order_id = order.get("order_id")

            if order_status in ("canceled", "rejected"):
                logger.warning(
                    "Live order for %s was %s — skipping",
                    ticker, order_status,
                )
                return None

            if filled > 0:
                # Immediately (partially or fully) filled — use actual fill price.
                fill_cents  = order.get("yes_price") if decision.side == "yes" else order.get("no_price")
                entry_price = (fill_cents or limit_cents) / 100.0
                stake       = round(filled * entry_price, 2)
                logger.info(
                    "Live order filled: %s %s x%d @ %.3f  actual_stake=%.2f",
                    ticker, decision.side, filled, entry_price, stake,
                )
            else:
                # Order is resting in the book (status=executed, filled=0).
                # Record the trade at our limit price — the order is live and
                # will fill if a counterparty appears before close.
                stake = round(count * entry_price, 2)
                logger.info(
                    "Live order resting in book: %s %s x%d @ %.3f  order_id=%s",
                    ticker, decision.side, count, entry_price, order.get("order_id", "?"),
                )
        else:
            logger.info(
                "Paper trade: %s %s @ %.3f  stake=%.2f  available_cash_before=%.2f",
                ticker, decision.side, entry_price, stake, available_cash,
            )

        # ── Deduct from portfolio and persist ───────────────────────────────
        await update_balance(db, -stake)

        trade = Trade(
            market_id=ticker,
            market_title=market.get("title", "Unknown market"),
            sport=sport,
            side=decision.side,
            stake=stake,
            entry_price=entry_price,
            status="open",
            ai_reasoning=decision.reasoning,
            confidence=decision.confidence,
            kalshi_order_id=order_id,
        )
        db.add(trade)
        await db.commit()
        await db.refresh(trade)
        return trade

    async def save_signal(self, db: AsyncSession, market_id: str, sport: str,
                          sentiment: float, rule_signal: float,
                          ai_recommendation: str,
                          consensus_prob: Optional[float] = None,
                          bookmaker_count: Optional[int] = None,
                          line_movement: Optional[str] = None,
                          yes_ask: Optional[float] = None) -> None:
        signal = MarketSignal(market_id=market_id, sport=sport,
                              news_sentiment=sentiment, rule_signal=rule_signal,
                              ai_recommendation=ai_recommendation,
                              consensus_prob=consensus_prob,
                              bookmaker_count=bookmaker_count,
                              line_movement=line_movement,
                              yes_ask=yes_ask)
        db.add(signal)
        await db.commit()

    async def resolve_trade(self, db: AsyncSession, trade_id: int,
                            outcome: str, exit_price: float) -> Optional[Trade]:
        result = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()
        if trade is None or trade.status != "open":
            return None

        if outcome == "win":
            payout = trade.stake / trade.entry_price
            pnl = payout - trade.stake
        else:
            payout = 0.0
            pnl = -trade.stake

        trade.exit_price = exit_price
        trade.status = "closed"
        trade.pnl = round(pnl, 2)
        trade.resolved_at = datetime.utcnow()
        await update_balance(db, payout)
        await db.commit()
        await db.refresh(trade)
        logger.info("Trade %d resolved: %s  P&L=%.2f", trade_id, outcome, pnl)
        return trade


trading_service = TradingService()
