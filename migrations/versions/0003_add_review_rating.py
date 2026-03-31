"""add rating column to reviews

Revision ID: 0003_add_review_rating
Revises: 0002_fix_columns
Create Date: 2026-03-31 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_add_review_rating"
down_revision = "0002_fix_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reviews", sa.Column("rating", sa.Integer(), nullable=False, server_default="5"))


def downgrade() -> None:
    op.drop_column("reviews", "rating")
