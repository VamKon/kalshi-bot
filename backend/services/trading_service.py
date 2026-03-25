"""
Core trading logic:
  - Kelly Criterion position sizing
  - Paper trade execution & persistence
  - Trade resolution (mark win/loss on market close)
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.db_models import Portfolio, Trade, MarketSignal
from backend.models.schemas import AIDecision

logger = logging.getLogger(__name__)


# ── Kelly Criterion ────────────────────────────────────────────────────────

def kelly_stake(
    bankroll: float,
    probability: float,
    odds: float,
    kelly_fraction: float = settings.KELLY_FRACTION,
) -> float:
    """
    Full Kelly formula capped at MAX_TRADE_PCT of bankroll and MAX_TRADE_USD.

    Args:
        bankroll:       Current portfolio balance in USD.
        probability:    Estimated true probability of the event (0–1).
        odds:           Decimal odds (e.g. 1/yes_ask - 1 for the payout).
        kelly_fraction: Fractional Kelly multiplier (default 0.25).

    Returns:
        Dollar amount to stake (always positive, ≥ 0).
    """
    if odds <= 0 or probability <= 0:
        return 0.0

    # Kelly formula: f* = (bp - q) / b  where b = decimal odds, p = win prob, q = 1-p
    q = 1.0 - probability
    f_star = (odds * probability - q) / odds

    if f_star <= 0:
        return 0.0  # Negative edge — don't trade

    # Apply fractional Kelly
    f_fractional = f_star * kelly_fraction

    # Cap at max percentage of bankroll
    max_pct_stake = bankroll * settings.MAX_TRADE_PCT
    stake = min(f_fractional * bankroll, max_pct_stake, settings.MAX_TRADE_USD)

    return round(max(stake, 0.0), 2)


# ── Portfolio helpers ─────────────────────────────────────────────────────

async def get_or_create_portfolio(db: AsyncSession) -> Portfolio:
    result = await db.execute(select(Portfolio).limit(1))
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(balance=settings.INITIAL_BANKROLL)
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)
    return portfolio


async def update_balance(db: AsyncSession, delta: float) -> None:
    """Add delta (positive or negative) to the portfolio balance."""
    portfolio = await get_or_create_portfolio(db)
    portfolio.balance = max(0.0, portfolio.balance + delta)
    portfolio.updated_at = datetime.utcnow()
    await db.commit()


# ── Trade execution ────────────────────────────────────────────────────────

class TradingService:

    async def execute_paper_trade(
        self,
        db: AsyncSession,
        market: dict,
        sport: str,
        decision: AIDecision,
        entry_price: float,
    ) -> Optional[Trade]:
        """
        Execute a paper trade: size via Kelly, deduct from portfolio, persist.
        Returns the Trade ORM object or None if not traded.
        """
        if not decision.trade:
            return None
        if decision.confidence < settings.MIN_CONFIDENCE:
            logger.info(
                "Skipping trade — confidence %.2f below threshold %.2f",
                decision.confidence,
                settings.MIN_CONFIDENCE,
            )
            return None

        portfolio = await get_or_create_portfolio(db)

        # Decimal odds for a binary YES/NO market:  payout = 1/price - 1
        if entry_price <= 0 or entry_price >= 1:
            logger.warning("Invalid entry price: %.3f — skipping", entry_price)
            return None

        odds = (1.0 / entry_price) - 1.0
        stake = kelly_stake(portfolio.balance, decision.confidence, odds)

        if stake < 0.50:
            logger.info("Stake %.2f too small — skipping trade", stake)
            return None

        if stake > portfolio.balance:
            logger.warning("Insufficient balance (%.2f) — skipping", portfolio.balance)
            return None

        # Deduct stake from virtual bankroll
        await update_balance(db, -stake)

        trade = Trade(
            market_id=market.get("ticker", "UNKNOWN"),
            market_title=market.get("title", "Unknown market"),
            sport=sport,
            side=decision.side,
            stake=stake,
            entry_price=entry_price,
            status="open",
            ai_reasoning=decision.reasoning,
            confidence=decision.confidence,
        )
        db.add(trade)
        await db.commit()
        await db.refresh(trade)

        logger.info(
            "Paper trade placed: %s %s @ %.2f stake=%.2f",
            market.get("ticker"),
            decision.side,
            entry_price,
            stake,
        )
        return trade

    async def save_signal(
        self,
        db: AsyncSession,
        market_id: str,
        sport: str,
        sentiment: float,
        rule_signal: float,
        ai_recommendation: str,
    ) -> None:
        """Persist a market signal record."""
        signal = MarketSignal(
            market_id=market_id,
            sport=sport,
            news_sentiment=sentiment,
            rule_signal=rule_signal,
            ai_recommendation=ai_recommendation,
        )
        db.add(signal)
        await db.commit()

    async def resolve_trade(
        self,
        db: AsyncSession,
        trade_id: int,
        outcome: str,  # "win" | "loss"
        exit_price: float,
    ) -> Optional[Trade]:
        """
        Close an open trade, compute P&L, and update portfolio balance.
        outcome: "win" means the side we bet on resolved correctly.
        """
        result = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()
        if trade is None or trade.status != "open":
            return None

        if outcome == "win":
            # Payout = stake / entry_price (full contract value)
            payout = trade.stake / trade.entry_price
            pnl = payout - trade.stake
        else:
            payout = 0.0
            pnl = -trade.stake

        trade.exit_price = exit_price
        trade.status = "closed"
        trade.pnl = round(pnl, 2)
        trade.resolved_at = datetime.utcnow()

        # Return payout to portfolio
        await update_balance(db, payout)

        await db.commit()
        await db.refresh(trade)
        logger.info("Trade %d resolved: %s  P&L=%.2f", trade_id, outcome, pnl)
        return trade


trading_service = TradingService()
