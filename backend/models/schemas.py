"""
Pydantic schemas for API request/response validation.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# ── Portfolio ──────────────────────────────────────────────────────────────

class PortfolioOut(BaseModel):
    id: int
    balance: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ── Trade ──────────────────────────────────────────────────────────────────

class TradeOut(BaseModel):
    id: int
    market_id: str
    market_title: str
    sport: str
    side: str
    stake: float
    entry_price: float
    exit_price: Optional[float]
    status: str
    pnl: Optional[float]
    ai_reasoning: Optional[str]
    confidence: Optional[float]
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Market Signal ──────────────────────────────────────────────────────────

class MarketSignalOut(BaseModel):
    id: int
    market_id: str
    sport: str
    news_sentiment: Optional[float]
    rule_signal: Optional[float]
    ai_recommendation: Optional[str]
    scanned_at: datetime

    class Config:
        from_attributes = True


# ── Market (from Kalshi API) ────────────────────────────────────────────────

class MarketInfo(BaseModel):
    """Lightweight representation of a Kalshi market."""
    ticker: str
    title: str
    sport: str
    status: str
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    volume: Optional[float] = None
    close_time: Optional[datetime] = None
    signal_strength: Optional[float] = None   # combined AI signal 0-1


# ── AI Decision ────────────────────────────────────────────────────────────

class AIDecision(BaseModel):
    trade: bool
    side: str           # "yes" | "no"
    confidence: float   # 0–1
    reasoning: str


# ── Scan result ────────────────────────────────────────────────────────────

class ScanResult(BaseModel):
    markets_scanned: int
    trades_placed: int
    timestamp: datetime
