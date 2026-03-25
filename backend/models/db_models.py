"""
SQLAlchemy ORM models matching the PostgreSQL schema in plan.txt.
"""
import uuid
from datetime import datetime

from sqlalchemy import String, Float, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base


def _now() -> datetime:
    return datetime.utcnow()


class Portfolio(Base):
    """Single-row table that tracks the virtual (paper) portfolio balance."""
    __tablename__ = "portfolio"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


class Trade(Base):
    """Every paper trade — open, closed, or cancelled."""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    market_title: Mapped[str] = mapped_column(String(512), nullable=False)
    sport: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)   # "yes" | "no"
    stake: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", index=True
    )   # open | closed | cancelled
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MarketSignal(Base):
    """AI + rule-based signal snapshot for each scanned market."""
    __tablename__ = "market_signals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(32), nullable=False)
    news_sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    rule_signal: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
