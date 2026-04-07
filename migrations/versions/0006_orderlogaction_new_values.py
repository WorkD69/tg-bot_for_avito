"""orderlogaction: add requisites_sent and dispute_opened

Revision ID: 0006_orderlogaction_new_values
Revises: 0005_schema_fixes
Create Date: 2026-04-06 00:00:00.000000
"""
from alembic import op

revision = "0006_orderlogaction_new_values"
down_revision = "0005_schema_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enums cannot be altered via SQLAlchemy Enum.create() after the fact.
    # Use raw ALTER TYPE … ADD VALUE — idempotent in PG 14+ with IF NOT EXISTS.
    op.execute("ALTER TYPE orderlogaction ADD VALUE IF NOT EXISTS 'requisites_sent'")
    op.execute("ALTER TYPE orderlogaction ADD VALUE IF NOT EXISTS 'dispute_opened'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # Downgrade is a no-op — the values remain but are unused.
    pass
