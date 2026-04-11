"""earningstatus: add on_hold value + frozen_at / frozen_by_id columns

Revision ID: 0008_earningstatus_on_hold
Revises: 0007_operator_earnings
Create Date: 2026-04-12 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_earningstatus_on_hold"
down_revision = "0007_operator_earnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new enum value — idempotent in PG 14+ with IF NOT EXISTS
    op.execute("ALTER TYPE earningstatus ADD VALUE IF NOT EXISTS 'on_hold'")

    # Add audit columns for freeze events
    op.add_column(
        "operator_earnings",
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "operator_earnings",
        sa.Column(
            "frozen_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("operator_earnings", "frozen_by_id")
    op.drop_column("operator_earnings", "frozen_at")
    # PostgreSQL does not support removing enum values — downgrade leaves on_hold in the type
