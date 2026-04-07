"""Add venue column to market_signals

Revision ID: 006
Revises: 005
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "006_add_venue_to_signals"
down_revision = "005_add_yes_ask_to_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_signals",
        sa.Column("venue", sa.String(256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_signals", "venue")
