"""Add away_team and consensus_away to sportsbook_odds.

Revision ID: 004_add_away_team_to_sportsbook_odds
Revises: 003_add_odds_detail_to_signals
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = "004_add_away_team_to_sportsbook_odds"
down_revision = "003_add_odds_detail_to_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sportsbook_odds",
        sa.Column("away_team", sa.String(128), nullable=True),
    )
    op.add_column(
        "sportsbook_odds",
        sa.Column("consensus_away", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sportsbook_odds", "consensus_away")
    op.drop_column("sportsbook_odds", "away_team")
