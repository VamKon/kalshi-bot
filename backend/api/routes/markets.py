"""
Markets endpoint — returns live monitored markets with signal strength.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.models.db_models import MarketSignal
from backend.models.schemas import MarketInfo
from backend.services.kalshi_client import kalshi_client

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("")
async def list_markets(
    sport: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Return open Kalshi markets for monitored sports enriched with the latest
    AI signal strength from the database.
    """
    raw_markets = await kalshi_client.get_markets(status="open", limit=200)

    # Build a ticker → latest signal map from DB
    stmt = (
        select(MarketSignal)
        .order_by(desc(MarketSignal.scanned_at))
        .limit(500)
    )
    sig_result = await db.execute(stmt)
    signals: dict[str, MarketSignal] = {}
    for sig in sig_result.scalars().all():
        if sig.market_id not in signals:
            signals[sig.market_id] = sig

    markets: list[MarketInfo] = []
    for m in raw_markets:
        detected_sport = kalshi_client.classify_sport(m)
        if detected_sport is None:
            continue
        if sport and detected_sport != sport:
            continue

        yes_bid, yes_ask = kalshi_client.extract_best_price(m)
        sig = signals.get(m.get("ticker", ""))
        signal_strength = None
        if sig:
            # Combine news + rule signals into 0-1 score
            raw = ((sig.news_sentiment or 0) + (sig.rule_signal or 0)) / 2
            signal_strength = round((raw + 1) / 2, 3)  # normalise to [0,1]

        markets.append(
            MarketInfo(
                ticker=m.get("ticker", ""),
                title=m.get("title", ""),
                sport=detected_sport,
                status=m.get("status", "open"),
                yes_bid=round(yes_bid, 3),
                yes_ask=round(yes_ask, 3),
                volume=m.get("volume"),
                close_time=m.get("close_time"),
                signal_strength=signal_strength,
            )
        )

    return markets
