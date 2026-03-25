from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, cast, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.db_models import Trade
from app.models.schemas import BalanceUpdate, BalanceUpdateResult
from app.services.trading_service import get_or_create_portfolio, get_deployed_stake

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
async def get_portfolio(db: AsyncSession = Depends(get_db)):
    portfolio = await get_or_create_portfolio(db)
    closed = await db.execute(
        select(
            func.count(Trade.id).label("total_closed"),
            func.coalesce(func.sum(Trade.pnl), 0).label("total_pnl"),
            func.coalesce(func.sum(cast(Trade.pnl > 0, Integer)), 0).label("wins"),
        ).where(Trade.status == "closed")
    )
    row = closed.one()
    active = (await db.execute(select(func.count(Trade.id)).where(Trade.status == "open"))).scalar_one()
    total_closed = row.total_closed or 0
    total_pnl    = float(row.total_pnl or 0)
    wins         = int(row.wins or 0)

    # Capital currently deployed in open trades
    deployed       = round(await get_deployed_stake(db), 2)
    available_cash = round(max(0.0, portfolio.balance - deployed), 2)

    return {
        "balance":             portfolio.balance,
        "deployed":            deployed,
        "available_cash":      available_cash,
        "initial_bankroll":    settings.INITIAL_BANKROLL,
        "total_pnl":           round(total_pnl, 2),
        "roi_pct":             round(total_pnl / settings.INITIAL_BANKROLL * 100, 2),
        "win_rate_pct":        round(wins / total_closed * 100, 1) if total_closed else 0.0,
        "total_closed_trades": total_closed,
        "active_trades":       active,
        "updated_at":          portfolio.updated_at,
    }


@router.put("/balance", response_model=BalanceUpdateResult)
async def update_balance(body: BalanceUpdate, db: AsyncSession = Depends(get_db)):
    """
    Manually set the portfolio balance.
    Useful for topping up the paper trading bankroll without touching the DB directly.
    """
    if body.balance <= 0:
        raise HTTPException(status_code=422, detail="balance must be greater than 0")

    portfolio = await get_or_create_portfolio(db)
    old_balance = portfolio.balance

    portfolio.balance = round(body.balance, 2)
    portfolio.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(portfolio)

    return BalanceUpdateResult(
        old_balance=round(old_balance, 2),
        new_balance=portfolio.balance,
        updated_at=portfolio.updated_at,
    )
