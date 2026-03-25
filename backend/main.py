"""
FastAPI application entry-point.

Starts:
  - The FastAPI app with all routers mounted under /api/v1
  - APScheduler to trigger market scans every N hours (configurable)
  - SQLAlchemy async engine (tables created on startup)
"""
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import health, markets, portfolio, scan, trades, settings_route
from backend.core.config import settings
from backend.core.database import engine, Base
from backend.services.scanner import scanner

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Scheduler ──────────────────────────────────────────────────────────────
_scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup and shutdown logic."""
    # Create DB tables (idempotent; Alembic handles migrations in prod)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured.")

    # Schedule recurring scan
    _scheduler.add_job(
        scanner.run,
        trigger="interval",
        hours=settings.SCAN_INTERVAL_HOURS,
        id="market_scan",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — scanning every %d hours.", settings.SCAN_INTERVAL_HOURS
    )

    yield  # ── app is running ──────────────────────────────────────────────

    _scheduler.shutdown(wait=False)
    await engine.dispose()
    logger.info("Shutdown complete.")


# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten for production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all routers under /api/v1
API_PREFIX = "/api/v1"
for router_module in (health, portfolio, trades, markets, scan, settings_route):
    app.include_router(router_module.router, prefix=API_PREFIX)
