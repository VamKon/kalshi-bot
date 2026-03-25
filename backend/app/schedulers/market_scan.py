"""
APScheduler job definition for the recurring market scan.
The scheduler is started/stopped via the FastAPI lifespan hook in main.py.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.services.scanner import scanner

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Register the scan job and start the scheduler."""
    _scheduler.add_job(
        scanner.run,
        trigger="interval",
        hours=settings.SCAN_INTERVAL_HOURS,
        id="market_scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started — scanning every %d hours.",
                settings.SCAN_INTERVAL_HOURS)


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")
