"""
Application configuration — all values injectable via environment variables.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────────────────────────────
    APP_NAME: str = "Kalshi Trading Bot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://kalshi:kalshi@localhost:5432/kalshi_bot"

    # ── Kalshi API ─────────────────────────────────────────────────────────
    KALSHI_API_BASE_URL: str = "https://demo-api.kalshi.co/trade-api/v2"
    KALSHI_API_KEY: Optional[str] = None          # injected as k8s secret later

    # ── Anthropic ─────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str                         # required — k8s secret

    # ── News (optional) ───────────────────────────────────────────────────
    NEWS_API_KEY: Optional[str] = None             # newsapi.org key

    # ── Trading parameters ────────────────────────────────────────────────
    PAPER_TRADING: bool = True
    INITIAL_BANKROLL: float = 1000.0
    KELLY_FRACTION: float = 0.25                   # fractional Kelly
    MAX_TRADE_PCT: float = 0.05                    # 5 % of bankroll
    MAX_TRADE_USD: float = 50.0                    # hard-cap per trade
    MIN_CONFIDENCE: float = 0.60                   # AI confidence threshold

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_HOURS: int = 12

    # ── Sports to monitor ─────────────────────────────────────────────────
    MONITORED_SPORTS: list[str] = ["NFL", "NBA", "MLS", "IPL"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
