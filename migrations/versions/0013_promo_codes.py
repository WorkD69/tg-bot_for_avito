"""promo_codes: add promo_codes table and promo fields on orders

Revision ID: 0013_promo_codes
Revises: 0012_file_captions
Create Date: 2026-04-28 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_promo_codes"
down_revision = "0012_file_captions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("first_order_only", sa.Boolean(), nullable=False, server_default="true"),
    )

    op.add_column("orders", sa.Column("applied_promo", sa.String(64), nullable=True))
    op.add_column("orders", sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True))

    # Seed the first promo code
    op.execute(
        "INSERT INTO promo_codes (code, discount_percent, is_active, first_order_only) "
        "VALUES ('WELCOME5', 5.00, true, true)"
    )


def downgrade() -> None:
    op.drop_column("orders", "discount_percent")
    op.drop_column("orders", "applied_promo")
    op.drop_table("promo_codes")
