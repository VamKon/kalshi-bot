import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.db_models import MarketSignal
from app.models.schemas import MarketInfo
from app.services.kalshi_client import kalshi_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["markets"])


async def _load_signals(db: AsyncSession) -> dict[str, MarketSignal]:
    """
    Load the most recent market signal per ticker from market_signals.
    Returns an empty dict if the table or any column doesn't exist yet (pre-migration).
    """
    try:
        result = await db.execute(
            select(MarketSignal).order_by(desc(MarketSignal.scanned_at)).limit(500)
        )
        signals: dict[str, MarketSignal] = {}
        for sig in result.scalars().all():
            if sig.market_id not in signals:
                signals[sig.market_id] = sig
        return signals
    except Exception as exc:
        logger.warning("Could not load market_signals (schema may need migration): %s", exc)
        await db.rollback()
        return {}


@router.get("")
async def list_markets(sport: str | None = None, db: AsyncSession = Depends(get_db)):
    raw_markets = await kalshi_client.get_markets(limit=200)
    signals = await _load_signals(db)

    markets = []
    for m in raw_markets:
        detected_sport = kalshi_client.classify_sport(m)
        if detected_sport is None:
            continue
        if sport and detected_sport != sport:
            continue

        yes_bid, yes_ask = kalshi_client.extract_best_price(m)
        sig = signals.get(m.get("ticker", ""))

        # Signal strength from news + rule signals
        signal_strength = None
        if sig:
            raw = ((sig.news_sentiment or 0) + (sig.rule_signal or 0)) / 2
            signal_strength = round((raw + 1) / 2, 3)

        # Odds data — all sourced from market_signals (populated during scan)
        consensus_prob  = None
        edge_pct        = None
        bookmaker_count = None
        line_movement   = None

        if sig:
            try:
                consensus_prob  = sig.consensus_prob
                bookmaker_count = sig.bookmaker_count
                line_movement   = sig.line_movement
            except Exception:
                pass  # columns not yet migrated — safe to ignore

        if consensus_prob is not None and yes_ask is not None:
            edge_pct = round(consensus_prob - yes_ask, 4)

        markets.append(MarketInfo(
            ticker=m.get("ticker", ""), title=m.get("title", ""),
            sport=detected_sport, status=m.get("status", "open"),
            yes_bid=round(yes_bid, 3), yes_ask=round(yes_ask, 3),
            volume=m.get("volume"), close_time=m.get("close_time"),
            signal_strength=signal_strength,
            consensus_prob=consensus_prob,
            edge_pct=edge_pct,
            line_movement=line_movement,
            bookmaker_count=bookmaker_count,
        ))
    return markets
