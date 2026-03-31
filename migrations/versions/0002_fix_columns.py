"""fix column names and drop stale columns

Revision ID: 0002_fix_columns
Revises: 0001_initial
Create Date: 2026-03-30 00:00:00.000000
"""
from alembic import op

revision = "0002_fix_columns"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # order_files: file_id → telegram_file_id
    op.alter_column("order_files", "file_id", new_column_name="telegram_file_id")

    # solution_files: file_id → telegram_file_id, drop stale file_type
    op.alter_column("solution_files", "file_id", new_column_name="telegram_file_id")
    op.drop_column("solution_files", "file_type")

    # reviews: drop stale rating column
    op.drop_column("reviews", "rating")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column("reviews", sa.Column("rating", sa.Integer(), nullable=True))
    op.add_column("solution_files", sa.Column("file_type", sa.String(), nullable=True))
    op.alter_column("solution_files", "telegram_file_id", new_column_name="file_id")
    op.alter_column("order_files", "telegram_file_id", new_column_name="file_id")
