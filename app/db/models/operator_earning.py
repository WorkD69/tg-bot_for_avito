import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.user import User


class EarningStatus(str, enum.Enum):
    pending = "pending"      # not yet paid out — safe to include in /payouts
    on_hold = "on_hold"      # frozen / disputed / requires admin review — NEVER auto-paid
    adjusted = "adjusted"    # amount manually overridden — payable (share was corrected)
    paid = "paid"            # admin marked as paid
    excluded = "excluded"    # permanently excluded from payout (dispute resolved against op)


# Statuses that represent payable earnings (safe to include in /payouts and /markpaid).
# on_hold is intentionally excluded — must be resolved first.
# excluded is intentionally excluded — permanently removed.
PAYABLE_STATUSES: tuple[EarningStatus, ...] = (EarningStatus.pending, EarningStatus.adjusted)


class OperatorEarning(Base, TimestampMixin):
    """One row per completed order — tracks what the operator earns and payment status.

    gross_amount   — what the client paid (= order.payment_amount at completion time)
    operator_share — what operator receives (gross * payout_percent / 100)
    payout_percent — percent used at calculation time (stored for audit; survives config changes)
    status         — pending | on_hold | adjusted | paid | excluded
    note           — admin comment: freeze reason, adjustment reason, payment note
    frozen_by_id   — who froze this earning (admin user.id); set when status→on_hold
    frozen_at      — when it was frozen
    """
    __tablename__ = "operator_earnings"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    operator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True
    )
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    operator_share: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payout_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    status: Mapped[EarningStatus] = mapped_column(
        Enum(EarningStatus, name="earningstatus"),
        nullable=False,
        default=EarningStatus.pending,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship("Order", foreign_keys=[order_id])
    operator: Mapped["User"] = relationship("User", foreign_keys=[operator_id])
    paid_by: Mapped["User | None"] = relationship("User", foreign_keys=[paid_by_id])
    frozen_by: Mapped["User | None"] = relationship("User", foreign_keys=[frozen_by_id])
