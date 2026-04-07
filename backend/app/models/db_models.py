"""
SQLAlchemy ORM models.
"""
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Text, Integer, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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
    # Kalshi YES ask at scan time — used to detect price movement between scans
    yes_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Venue derived from Odds API home_team (e.g. "Wankhede Stadium, Mumbai")
    venue: Mapped[str | None] = mapped_column(String(256), nullable=True)
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


class MatchEvent(Base):
    """
    One row per unique Odds API event key.
    Stores match metadata (venue, format, teams) for all cricket competitions.
    Upserted on each odds fetch; used to enrich the AI prompt with verified context.
    """
    __tablename__ = "match_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_match_events_event_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    sport_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    competition: Mapped[str | None] = mapped_column(String(255), nullable=True)
    home_team: Mapped[str] = mapped_column(String(255), nullable=False)
    away_team: Mapped[str] = mapped_column(String(255), nullable=False)
    commence_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    venue_city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    venue_country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    match_format: Mapped[str | None] = mapped_column(String(20), nullable=True)  # T20 | ODI | Test
    is_neutral_venue: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CricketFacts(Base):
    """
    Structured cricket match facts extracted by OpenRouter/Llama from articles.
    One row per event_key; expires at match start + 4 hours.
    """
    __tablename__ = "cricket_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Playing XI
    home_playing_xi: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    away_playing_xi: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    xi_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # confirmed|probable|unknown

    # Toss
    toss_winner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    toss_decision: Mapped[str | None] = mapped_column(String(10), nullable=True)  # bat|field

    # Conditions
    pitch_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    weather: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dew_factor: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Availability
    injuries: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    late_changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Form context
    key_player_form: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    head_to_head_venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    recent_form_home: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "WWLWW"
    recent_form_away: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Test-specific
    day_of_match: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session: Mapped[str | None] = mapped_column(String(20), nullable=True)
    follow_on_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Metadata
    source_urls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
