"""Add bookmaker_count and line_movement to market_signals.

Revision ID: 003_add_odds_detail_to_signals
Revises: 002_add_sportsbook_odds
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

revision = "003_add_odds_detail_to_signals"
down_revision = "002_add_sportsbook_odds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_signals",
        sa.Column("bookmaker_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "market_signals",
        sa.Column("line_movement", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_signals", "line_movement")
    op.drop_column("market_signals", "bookmaker_count")
