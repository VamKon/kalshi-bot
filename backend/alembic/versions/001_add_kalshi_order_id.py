"""Add kalshi_order_id to trades table

Revision ID: 001
Revises:
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("kalshi_order_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trades", "kalshi_order_id")
