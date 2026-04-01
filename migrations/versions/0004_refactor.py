"""refactor: order flags, review status, bid unique, solution file_type, order_logs, messagedirection fix

Revision ID: 0004_refactor
Revises: 0003_add_review_rating
Create Date: 2026-03-31 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_refactor"
down_revision = "0003_add_review_rating"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Fix messagedirection enum values to match DB (0001 used client_to_operator/operator_to_client)
    # The enum in DB was created by 0001 as client_to_operator/operator_to_client.
    # Python model had wrong short names. Now syncing model to match DB — no schema change needed,
    # just ensuring Python enum .value matches stored string. (No SQL needed here.)

    # ── 2. Add new columns to orders ─────────────────────────────────────────
    op.add_column("orders", sa.Column("payment_received_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("payment_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("solution_uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("orders", sa.Column("payment_revision", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("cancelled_by", sa.String(16), nullable=True))

    # ── 3. Add file_type to solution_files ────────────────────────────────────
    op.add_column("solution_files", sa.Column("file_type", sa.String(32), nullable=False, server_default="document"))

    # ── 4. Replace is_approved (bool) with status enum in reviews ────────────
    reviewstatus = sa.Enum("pending", "approved", "rejected", name="reviewstatus")
    reviewstatus.create(op.get_bind(), checkfirst=True)

    op.add_column("reviews", sa.Column(
        "status",
        sa.Enum("pending", "approved", "rejected", name="reviewstatus"),
        nullable=False,
        server_default="pending",
    ))
    op.add_column("reviews", sa.Column("moderated_by", sa.Integer(), nullable=True))
    op.add_column("reviews", sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True))

    # Migrate existing data: is_approved=true → approved, false → pending
    op.execute("UPDATE reviews SET status = 'approved' WHERE is_approved = true")
    op.execute("UPDATE reviews SET status = 'pending' WHERE is_approved = false")

    op.drop_column("reviews", "is_approved")

    op.create_foreign_key(
        "fk_reviews_moderated_by_users",
        "reviews", "users",
        ["moderated_by"], ["id"],
        ondelete="SET NULL",
    )

    # Unique constraint: one review per (order_id, client_id)
    op.create_unique_constraint("uq_review_order_client", "reviews", ["order_id", "client_id"])

    # ── 5. Dedup + unique constraints ────────────────────────────────────────
    # Remove duplicate bids (same operator, same order) — keep earliest (lowest id)
    # Tie-breaking rule: earliest bid wins the auction, so we keep MIN(id) = earliest.
    op.execute("""
        DELETE FROM bids
        WHERE id NOT IN (
            SELECT MIN(id) FROM bids GROUP BY order_id, operator_id
        )
    """)
    op.create_unique_constraint("uq_bid_order_operator", "bids", ["order_id", "operator_id"])

    # Remove duplicate reviews (same client, same order) — keep earliest
    op.execute("""
        DELETE FROM reviews
        WHERE id NOT IN (
            SELECT MIN(id) FROM reviews GROUP BY order_id, client_id
        )
    """)

    # ── 6. Create order_logs table ────────────────────────────────────────────
    orderlogaction = sa.Enum(
        "created", "bid_placed", "auction_closed", "operator_assigned",
        "payment_received", "payment_confirmed", "price_updated",
        "solution_uploaded", "completed", "cancelled",
        "comment_added", "files_added",
        name="orderlogaction",
    )
    orderlogaction.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "order_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "created", "bid_placed", "auction_closed", "operator_assigned",
                "payment_received", "payment_confirmed", "price_updated",
                "solution_uploaded", "completed", "cancelled",
                "comment_added", "files_added",
                name="orderlogaction",
            ),
            nullable=False,
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_logs_order_id", "order_logs", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_logs")
    op.execute("DROP TYPE IF EXISTS orderlogaction")

    op.drop_constraint("uq_bid_order_operator", "bids", type_="unique")
    op.drop_constraint("uq_review_order_client", "reviews", type_="unique")
    op.drop_constraint("fk_reviews_moderated_by_users", "reviews", type_="foreignkey")
    op.drop_column("reviews", "moderated_at")
    op.drop_column("reviews", "moderated_by")
    op.add_column("reviews", sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.execute("UPDATE reviews SET is_approved = true WHERE status = 'approved'")
    op.drop_column("reviews", "status")
    op.execute("DROP TYPE IF EXISTS reviewstatus")

    op.drop_column("solution_files", "file_type")
    op.drop_column("orders", "cancelled_by")
    op.drop_column("orders", "payment_revision")
    op.drop_column("orders", "solution_uploaded_at")
    op.drop_column("orders", "payment_confirmed_at")
    op.drop_column("orders", "payment_received_at")
