from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db

router = APIRouter(prefix="/health", tags=["health"])

@router.get("")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
    return {
        "status": "ok",
        "version": settings.APP_VERSION,
        "paper_trading": settings.PAPER_TRADING,
        "monitored_sports": settings.MONITORED_SPORTS,
        "db": db_status,
    }
