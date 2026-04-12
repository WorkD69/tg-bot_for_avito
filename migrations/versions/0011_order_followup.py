"""order_followup: track follow-up message sent after order completion

Revision ID: 0011_order_followup
Revises: 0010_user_campaign
Create Date: 2026-04-12 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_order_followup"
down_revision = "0010_user_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("followup_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "followup_sent_at")
