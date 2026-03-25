"""
Portfolio endpoint — returns current balance + aggregated stats.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_db
from backend.models.db_models import Portfolio, Trade
from backend.services.trading_service import get_or_create_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    """Return portfolio balance plus aggregated P&L stats."""
    portfolio = await get_or_create_portfolio(db)

    # Aggregated stats from closed trades
    closed_result = await db.execute(
        select(
            func.count(Trade.id).label("total_closed"),
            func.coalesce(func.sum(Trade.pnl), 0).label("total_pnl"),
            func.coalesce(
                func.sum(func.cast(Trade.pnl > 0, type_=func.Float)), 0
            ).label("wins"),
        ).where(Trade.status == "closed")
    )
    row = closed_result.one()

    open_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.status == "open")
    )
    active_count = open_result.scalar_one()

    total_closed = row.total_closed or 0
    total_pnl = float(row.total_pnl or 0)
    wins = int(row.wins or 0)
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    roi = (total_pnl / settings.INITIAL_BANKROLL * 100)

    return {
        "balance": portfolio.balance,
        "initial_bankroll": settings.INITIAL_BANKROLL,
        "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi, 2),
        "win_rate_pct": round(win_rate, 1),
        "total_closed_trades": total_closed,
        "active_trades": active_count,
        "updated_at": portfolio.updated_at,
    }
