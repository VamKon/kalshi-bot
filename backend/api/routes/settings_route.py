"""
Runtime settings endpoint — read current config and allow hot-patching select
parameters without a restart (in-memory only; restart reverts to env vars).
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from backend.core.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsPatch(BaseModel):
    scan_interval_hours: Optional[int] = None
    kelly_fraction: Optional[float] = None
    max_trade_usd: Optional[float] = None
    min_confidence: Optional[float] = None


@router.get("")
async def get_settings():
    return {
        "scan_interval_hours": settings.SCAN_INTERVAL_HOURS,
        "kelly_fraction": settings.KELLY_FRACTION,
        "max_trade_pct": settings.MAX_TRADE_PCT,
        "max_trade_usd": settings.MAX_TRADE_USD,
        "min_confidence": settings.MIN_CONFIDENCE,
        "paper_trading": settings.PAPER_TRADING,
        "monitored_sports": settings.MONITORED_SPORTS,
        "initial_bankroll": settings.INITIAL_BANKROLL,
    }


@router.patch("")
async def patch_settings(patch: SettingsPatch):
    """Hot-patch in-memory settings (resets on restart)."""
    if patch.scan_interval_hours is not None:
        settings.SCAN_INTERVAL_HOURS = patch.scan_interval_hours
    if patch.kelly_fraction is not None:
        settings.KELLY_FRACTION = max(0.01, min(patch.kelly_fraction, 1.0))
    if patch.max_trade_usd is not None:
        settings.MAX_TRADE_USD = max(1.0, patch.max_trade_usd)
    if patch.min_confidence is not None:
        settings.MIN_CONFIDENCE = max(0.0, min(patch.min_confidence, 1.0))
    return await get_settings()
