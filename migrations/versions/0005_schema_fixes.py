"""schema fixes: bids.amount precision, messages.text not null

Revision ID: 0005_schema_fixes
Revises: 0004_refactor
Create Date: 2026-03-31 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_schema_fixes"
down_revision = "0004_refactor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Align bids.amount precision: 0001 used Numeric(10,2), model expects Numeric(12,2) ──
    op.alter_column(
        "bids", "amount",
        type_=sa.Numeric(precision=12, scale=2),
        existing_type=sa.Numeric(precision=10, scale=2),
        existing_nullable=False,
    )

    # ── 2. Make messages.text NOT NULL (model: Mapped[str], but 0001 created nullable=True) ──
    # Backfill any nulls before adding the constraint (safe on empty/dev DB)
    op.execute("UPDATE messages SET text = '' WHERE text IS NULL")
    op.alter_column(
        "messages", "text",
        nullable=False,
        existing_type=sa.Text(),
    )


def downgrade() -> None:
    op.alter_column(
        "messages", "text",
        nullable=True,
        existing_type=sa.Text(),
    )
    op.alter_column(
        "bids", "amount",
        type_=sa.Numeric(precision=10, scale=2),
        existing_type=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
    )
