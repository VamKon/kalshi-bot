from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.db_models import Trade
from app.models.schemas import TradeOut
from app.services.trading_service import trading_service

router = APIRouter(prefix="/trades", tags=["trades"])

@router.get("", response_model=List[TradeOut])
async def list_trades(status: str | None = None, sport: str | None = None,
                      limit: int = 100, db: AsyncSession = Depends(get_db)):
    stmt = select(Trade).order_by(desc(Trade.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Trade.status == status)
    if sport:
        stmt = stmt.where(Trade.sport == sport)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/{trade_id}/resolve")
async def resolve_trade(trade_id: int, outcome: str, exit_price: float,
                        db: AsyncSession = Depends(get_db)):
    if outcome not in ("win", "loss"):
        raise HTTPException(status_code=422, detail="outcome must be 'win' or 'loss'")
    trade = await trading_service.resolve_trade(db, trade_id, outcome, exit_price)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found or already closed")
    return TradeOut.model_validate(trade)
