"""file_captions: add optional caption to solution_files and order_files

Revision ID: 0012_file_captions
Revises: 0011_order_followup
Create Date: 2026-04-15 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_file_captions"
down_revision = "0011_order_followup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "solution_files",
        sa.Column("caption", sa.Text(), nullable=True),
    )
    op.add_column(
        "order_files",
        sa.Column("caption", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("solution_files", "caption")
    op.drop_column("order_files", "caption")
