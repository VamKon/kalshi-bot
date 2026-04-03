"""Add yes_ask to market_signals for line movement tracking.

Revision ID: 005_add_yes_ask_to_signals
Revises: 004_add_away_team_to_sportsbook_odds
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "005_add_yes_ask_to_signals"
down_revision = "004_add_away_team_to_sportsbook_odds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # yes_ask at scan time — compared against the previous row to detect
    # Kalshi price movement between scans (smart money signal).
    op.add_column(
        "market_signals",
        sa.Column("yes_ask", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_signals", "yes_ask")
