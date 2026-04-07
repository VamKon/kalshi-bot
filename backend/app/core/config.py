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
    # Demo:       https://demo-api.kalshi.co/trade-api/v2   (sandbox — fewer markets)
    # Production: https://api.elections.kalshi.com/trade-api/v2  (all sports markets)
    # Note: "elections" subdomain is correct — it serves all Kalshi markets.
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

    # ── OpenRouter (optional) ──────────────────────────────────────────────────
    # Used for cost-effective cricket fact extraction via Llama 3 70B.
    # Falls back gracefully (no facts injected) when key is not set.
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_MODEL: str = "meta-llama/llama-3-70b-instruct"

    # ── Trading parameters ────────────────────────────────────────────────
    # Set PAPER_TRADING=false + KALSHI_API_BASE_URL=production URL to go live.
    # Also update INITIAL_BANKROLL to match real funding amount.
    # NOTE: env vars in values.yaml (k8s ConfigMap) always override these defaults.
    PAPER_TRADING: bool = True
    INITIAL_BANKROLL: float = 10.0             # sized for $10 live bankroll
    KELLY_FRACTION: float = 0.25              # fractional Kelly, conservative
    MAX_TRADE_PCT: float = 0.10               # 10% of available cash per trade
    MAX_TRADE_USD: float = 11.0               # hard-cap per trade in dollars
    MIN_CONFIDENCE: float = 0.55              # AI confidence gate threshold
    MIN_EDGE_THRESHOLD: float = 0.02          # minimum 2% edge to trade

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCAN_INTERVAL_HOURS: int = 2              # scan every 2 hours

    # ── Scan limits (cost optimisation) ───────────────────────────────────
    MAX_MARKETS_PER_SCAN: int = 20        # top N markets by volume per sport
    MARKET_PROB_MIN: float = 0.10         # skip near-certain NO (< 10%)
    MARKET_PROB_MAX: float = 0.90         # skip near-certain YES (> 90%)
    MARKET_HOURS_AHEAD: int = 16          # only trade games within 16 h
    MARKET_MIN_HOURS_AHEAD: float = 1.5   # skip games starting within 1.5 h
    MIN_MARKET_VOLUME: float = 100.0      # require meaningful open interest for live trading
    MAX_BID_ASK_SPREAD: float = 0.04      # skip illiquid markets (spread > 4%)

    # ── News cache ────────────────────────────────────────────────────────
    NEWS_CACHE_TTL_SECONDS: int = 21600   # 6 hours

    # ── Sports to monitor ─────────────────────────────────────────────────
    MONITORED_SPORTS: list[str] = ["Cricket"]

    # ── Market type filter ────────────────────────────────────────────────
    GAME_WINNER_ONLY: bool = True   # only trade game-winner markets (yes/no who wins)

    # ── Sportsbook coverage requirement ───────────────────────────────────
    # When True, skip any market where The Odds API finds no matching bookmaker
    # lines. This automatically blocks obscure markets (minnow cricket nations,
    # international friendlies, etc.) without needing a manual blocklist.
    # Falls back to AI-only edge when False (less reliable but more coverage).
    REQUIRE_SPORTSBOOK_ODDS: bool = True

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
