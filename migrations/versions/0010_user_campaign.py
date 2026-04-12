"""user_campaign: add campaign attribution field to users

Revision ID: 0010_user_campaign
Revises: 0009_user_source
Create Date: 2026-04-12 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_user_campaign"
down_revision = "0009_user_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("campaign", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "campaign")
