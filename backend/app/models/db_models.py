"""
SQLAlchemy ORM models.
"""
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class Portfolio(Base):
    __tablename__ = "portfolio"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    market_title: Mapped[str] = mapped_column(String(512), nullable=False)
    sport: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    kalshi_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MarketSignal(Base):
    __tablename__ = "market_signals"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(32), nullable=False)
    news_sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    consensus_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    bookmaker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_movement: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SportsbookOdds(Base):
    """
    Caches sportsbook odds from The Odds API with a 6-hour TTL.
    One row per bookmaker per event side; consensus_prob is the
    averaged vig-removed probability across all bookmakers.
    """
    __tablename__ = "sportsbook_odds"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)   # h2h|spread|total
    outcome: Mapped[str] = mapped_column(String(128), nullable=False)       # home team name
    away_team: Mapped[str | None] = mapped_column(String(128), nullable=True)  # away team name
    price: Mapped[float | None] = mapped_column(Float, nullable=True)       # American odds
    implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_away: Mapped[float | None] = mapped_column(Float, nullable=True)  # away consensus
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
