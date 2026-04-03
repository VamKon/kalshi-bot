from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.core.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])

ALL_SPORTS = ["NFL", "NBA", "MLS", "Cricket"]

class SettingsPatch(BaseModel):
    scan_interval_hours: Optional[int] = None
    kelly_fraction: Optional[float] = None
    max_trade_usd: Optional[float] = None
    max_trade_pct: Optional[float] = None
    min_confidence: Optional[float] = None
    monitored_sports: Optional[List[str]] = None
    game_winner_only: Optional[bool] = None

@router.get("")
async def get_settings():
    return {
        "scan_interval_hours": settings.SCAN_INTERVAL_HOURS,
        "kelly_fraction":      settings.KELLY_FRACTION,
        "max_trade_pct":       settings.MAX_TRADE_PCT,
        "max_trade_usd":       settings.MAX_TRADE_USD,
        "min_confidence": settings.MIN_CONFIDENCE,
        "min_edge_threshold": settings.MIN_EDGE_THRESHOLD,
        "paper_trading": settings.PAPER_TRADING,
        "monitored_sports": settings.MONITORED_SPORTS,
        "all_sports": ALL_SPORTS,
        "game_winner_only": settings.GAME_WINNER_ONLY,
        "initial_bankroll": settings.INITIAL_BANKROLL,
        "odds_api_key_configured": bool(settings.ODDS_API_KEY),
    }

@router.patch("")
async def patch_settings(patch: SettingsPatch):
    if patch.scan_interval_hours is not None:
        settings.SCAN_INTERVAL_HOURS = patch.scan_interval_hours
    if patch.kelly_fraction is not None:
        settings.KELLY_FRACTION = max(0.01, min(patch.kelly_fraction, 1.0))
    if patch.max_trade_usd is not None:
        settings.MAX_TRADE_USD = max(1.0, patch.max_trade_usd)
    if patch.max_trade_pct is not None:
        settings.MAX_TRADE_PCT = max(0.01, min(patch.max_trade_pct, 1.0))
    if patch.min_confidence is not None:
        settings.MIN_CONFIDENCE = max(0.0, min(patch.min_confidence, 1.0))
    if patch.monitored_sports is not None:
        # Validate against known sports; keep at least one
        valid = [s for s in patch.monitored_sports if s in ALL_SPORTS]
        if valid:
            settings.MONITORED_SPORTS = valid
    if patch.game_winner_only is not None:
        settings.GAME_WINNER_ONLY = patch.game_winner_only
    return await get_settings()
