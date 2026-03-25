"""
Pydantic schemas for API request/response validation.
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PortfolioOut(BaseModel):
    id: int
    balance: float
    created_at: datetime
    updated_at: datetime
    class Config:
        from_attributes = True


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


class MarketInfo(BaseModel):
    ticker: str
    title: str
    sport: str
    status: str
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    volume: Optional[float] = None
    close_time: Optional[datetime] = None
    signal_strength: Optional[float] = None
    # Sportsbook odds fields (populated when ODDS_API_KEY is set)
    consensus_prob: Optional[float] = None      # vig-removed sportsbook consensus
    edge_pct: Optional[float] = None            # consensus_prob - kalshi_yes_ask
    line_movement: Optional[str] = None         # human-readable movement description
    bookmaker_count: Optional[int] = None


class AIDecision(BaseModel):
    trade: bool
    side: str
    confidence: float
    reasoning: str


class ScanResult(BaseModel):
    markets_scanned: int
    trades_placed: int
    timestamp: datetime


class ResolveResult(BaseModel):
    trades_checked: int
    trades_resolved: int
    wins: int
    losses: int
    timestamp: datetime


class BalanceUpdate(BaseModel):
    balance: float


class BalanceUpdateResult(BaseModel):
    old_balance: float
    new_balance: float
    updated_at: datetime
