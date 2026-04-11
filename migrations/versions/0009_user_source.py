"""users: add source column for attribution tracking

Stores how the user first arrived (deeplink payload from /start).
Known values: 'avito', 'tg_channel', 'direct', 'unknown' (default).

Revision ID: 0009_user_source
Revises: 0008_earningstatus_on_hold
Create Date: 2026-04-12 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_user_source"
down_revision = "0008_earningstatus_on_hold"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "source")
