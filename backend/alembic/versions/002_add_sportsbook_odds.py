"""Add sportsbook_odds table and consensus_prob column to market_signals

Revision ID: 002_add_sportsbook_odds
Revises: 001_add_kalshi_order_id
Create Date: 2026-03-24
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "002_add_sportsbook_odds"
down_revision = "001_add_kalshi_order_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Add consensus_prob to market_signals ────────────────────────────
    op.add_column(
        "market_signals",
        sa.Column("consensus_prob", sa.Float(), nullable=True),
    )

    # ── 2. Create sportsbook_odds table ────────────────────────────────────
    op.create_table(
        "sportsbook_odds",
        sa.Column("id",            sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("market_id",     sa.String(128),   nullable=False),
        sa.Column("event_key",     sa.String(256),   nullable=False),
        sa.Column("sport",         sa.String(32),    nullable=False),
        sa.Column("bookmaker",     sa.String(64),    nullable=False),
        sa.Column("market_type",   sa.String(16),    nullable=False),
        sa.Column("outcome",       sa.String(128),   nullable=False),
        sa.Column("price",         sa.Float(),       nullable=True),
        sa.Column("implied_prob",  sa.Float(),       nullable=True),
        sa.Column("consensus_prob", sa.Float(),      nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index("ix_sportsbook_odds_market_id", "sportsbook_odds", ["market_id"])
    op.create_index("ix_sportsbook_odds_event_key",  "sportsbook_odds", ["event_key"])
    op.create_index("ix_sportsbook_odds_sport",      "sportsbook_odds", ["sport"])
    op.create_index("ix_sportsbook_odds_fetched_at", "sportsbook_odds", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_sportsbook_odds_fetched_at", table_name="sportsbook_odds")
    op.drop_index("ix_sportsbook_odds_sport",      table_name="sportsbook_odds")
    op.drop_index("ix_sportsbook_odds_event_key",  table_name="sportsbook_odds")
    op.drop_index("ix_sportsbook_odds_market_id",  table_name="sportsbook_odds")
    op.drop_table("sportsbook_odds")
    op.drop_column("market_signals", "consensus_prob")
