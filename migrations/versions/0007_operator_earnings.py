"""operator_earnings: payout tracking per completed order

Revision ID: 0007_operator_earnings
Revises: 0006_orderlogaction_new_values
Create Date: 2026-04-11 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_operator_earnings"
down_revision = "0006_orderlogaction_new_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create earningstatus enum first (PostgreSQL requires explicit type creation)
    op.execute(
        "CREATE TYPE earningstatus AS ENUM ('pending', 'paid', 'excluded', 'adjusted')"
    )

    op.create_table(
        "operator_earnings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "operator_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=False,
            index=True,
        ),
        sa.Column("gross_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("operator_share", sa.Numeric(12, 2), nullable=False),
        sa.Column("payout_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "paid", "excluded", "adjusted", name="earningstatus", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "paid_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("operator_earnings")
    op.execute("DROP TYPE IF EXISTS earningstatus")
