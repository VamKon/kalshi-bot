"""
APScheduler job definitions.

Jobs registered:
  market_scan   — full scan every SCAN_INTERVAL_HOURS (default 2h)
  toss_watcher  — polls ESPNcricinfo RSS every 10 min for toss results;
                  fires targeted mini-scans when a toss is detected for
                  an open Kalshi cricket market.  Cricket-only, low cost
                  (one RSS fetch, no Claude calls unless toss found).
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.services.scanner import scanner
from app.services.toss_watcher import toss_watcher

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Register all jobs and start the scheduler."""
    _scheduler.add_job(
        scanner.run,
        trigger="interval",
        hours=settings.SCAN_INTERVAL_HOURS,
        id="market_scan",
        replace_existing=True,
    )
    _scheduler.add_job(
        toss_watcher.check_and_trigger,
        trigger="interval",
        minutes=10,
        id="toss_watcher",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — market scan every %dh, toss watcher every 10min.",
        settings.SCAN_INTERVAL_HOURS,
    )


def stop_scheduler() -> None:
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")
