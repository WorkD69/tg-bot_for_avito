"""drop unused group_message_id column from orders

Revision ID: 0014_drop_group_message_id
Revises: 0013_promo_codes
Create Date: 2026-05-01 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_drop_group_message_id"
down_revision = "0013_promo_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("orders", "group_message_id")


def downgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("group_message_id", sa.BigInteger(), nullable=True),
    )
