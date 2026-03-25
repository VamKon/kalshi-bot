import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.services.scanner import scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("")
async def trigger_scan(background_tasks: BackgroundTasks):
    """
    Manually trigger a full market scan.

    Returns 202 immediately — the scan runs in the background.
    A full scan takes 1-3 minutes (series discovery + news + AI calls).
    Check the Trades and Markets pages for results after it completes.
    """
    background_tasks.add_task(_run_scan)
    return JSONResponse(
        status_code=202,
        content={
            "status": "started",
            "message": (
                "Scan is running in the background. "
                "Check the Active Trades page in 1–3 minutes for results."
            ),
        },
    )


async def _run_scan() -> None:
    """Background wrapper — logs errors so they don't silently disappear."""
    try:
        result = await scanner.run()
        logger.info(
            "Background scan complete — %d markets scanned, %d trades placed",
            result.markets_scanned, result.trades_placed,
        )
    except Exception as exc:
        logger.error("Background scan failed: %s", exc, exc_info=True)
