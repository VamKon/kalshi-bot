from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.alerts import alert_store
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

    # Last scan time — read from market_signals so the sidebar can show a countdown
    last_scan_at = None
    try:
        result = await db.execute(text("SELECT MAX(scanned_at) FROM market_signals"))
        row = result.fetchone()
        if row and row[0]:
            last_scan_at = row[0].isoformat()
    except Exception:
        pass  # table may not exist yet

    return {
        "status":            "ok",
        "version":           settings.APP_VERSION,
        "paper_trading":     settings.PAPER_TRADING,
        "monitored_sports":  settings.MONITORED_SPORTS,
        "scan_interval_hours": settings.SCAN_INTERVAL_HOURS,
        "last_scan_at":      last_scan_at,
        "db":                db_status,
        # Alert summary — sidebar uses these to decide whether to show the badge
        "error_count":       alert_store.error_count,
        "warning_count":     alert_store.warning_count,
        "alerts":            alert_store.get_unresolved(),
    }


@router.delete("/alerts")
async def clear_alerts():
    """Dismiss all current alerts."""
    alert_store.clear()
    return {"cleared": True}
