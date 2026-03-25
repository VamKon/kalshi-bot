"""
Application configuration — all values injectable via environment variables.
"""
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── App ────────────────────────────────────────────────────────────────
    APP_NAME: str = "Kalshi Trading Bot"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── Database ───────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://kalshi:kalshi@localhost:5432/kalshi_bot"

    # ── Kalshi API ─────────────────────────────────────────────────────────
    # Demo:       https://demo-api.kalshi.co/trade-api/v2   (default, paper only)
    # Production: https://trading-api.kalshi.co/trade-api/v2
    KALSHI_API_BASE_URL: str = "https://demo-api.kalshi.co/trade-api/v2"
    # RSA-based auth — key ID + PEM private key injected as k8s secrets
    KALSHI_KEY_ID: Optional[str] = None
    KALSHI_PRIVATE_KEY: Optional[str] = None   # PEM-encoded RSA private key

    # ── Anthropic ─────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str                     # required — k8s secret

    # ── News (optional) ───────────────────────────────────────────────────
    NEWS_API_KEY: Optional[str] = None         # newsapi.org key

    # ── The Odds API (optional) ────────────────────────────────────────────
    ODDS_API_KEY: Optional[str] = None         # the-odds-api.com key

    # ── Trading parameters ────────────────────────────────────────────────
    # Set PAPER_TRADING=false + KALSHI_API_BASE_URL=production URL to go live.
    # Also set MIN_MARKET_VOLUME=100 and update INITIAL_BANKROLL to real funding.
    PAPER_TRADING: bool = True
    INITIAL_BANKROLL: float = 1000.0
    KELLY_FRACTION: float = 0.25              # fractional Kelly, conservative
    MAX_TRADE_PCT: float = 0.05               # 5% of available cash per trade
    MAX_TRADE_USD: float = 50.0               # hard-cap per trade in dollars
    MIN_CONFIDENCE: float = 0.50             # AI confidence threshold
    MIN_EDGE_THRESHOLD: float = 0.03          # minimum 3% edge to trade

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_HOURS: int = 12

    # ── Scan limits (cost optimisation) ───────────────────────────────────
    MAX_MARKETS_PER_SCAN: int = 20        # top N markets by volume per sport
    MARKET_PROB_MIN: float = 0.10         # skip near-certain NO (< 10%)
    MARKET_PROB_MAX: float = 0.90         # skip near-certain YES (> 90%)
    MARKET_HOURS_AHEAD: int = 48          # only trade games within 48 h
    MARKET_MIN_HOURS_AHEAD: float = 1.5   # skip games starting within 1.5 h
    MIN_MARKET_VOLUME: float = 0.0        # DEMO: set to 100.0 for production
    MAX_BID_ASK_SPREAD: float = 0.06      # skip illiquid markets (spread > 6%)

    # ── News cache ────────────────────────────────────────────────────────
    NEWS_CACHE_TTL_SECONDS: int = 21600   # 6 hours

    # ── Sports to monitor ─────────────────────────────────────────────────
    MONITORED_SPORTS: list[str] = ["NFL", "NBA", "MLS", "Cricket"]

    # ── Market type filter ────────────────────────────────────────────────
    GAME_WINNER_ONLY: bool = True   # only trade game-winner markets (yes/no who wins)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
