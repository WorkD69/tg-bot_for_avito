import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.user import User
    from app.db.models.order_file import OrderFile
    from app.db.models.solution_file import SolutionFile
    from app.db.models.bid import Bid
    from app.db.models.review import Review
    from app.db.models.message import Message
    from app.db.models.operator_note import OperatorNote
    from app.db.models.order_log import OrderLog


class OrderStatus(str, enum.Enum):
    pending = "На рассмотрении"
    awaiting_payment = "Ожидает оплаты"
    in_progress = "В работе"
    completed = "Выполнено"
    cancelled = "Отменено"


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    operator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="orderstatus"),
        nullable=False,
        default=OrderStatus.pending,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    auction_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_invoice_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    # Kept for DB schema compatibility — no longer written or read by the application.
    # Column exists in migrations 0001+; removing it requires a new migration with op.drop_column.
    group_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Payment tracking flags (no new statuses — extra fields only)
    payment_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    solution_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cancelled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "client" | "operator" | "admin" | "system"
    # Growth: set when follow-up message is sent to client after completion (24h delay).
    # Acts as idempotency guard — NULL = not yet sent, non-NULL = sent (no retry).
    followup_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Promo: applied promo code and its discount. payment_amount stays = operator's bid;
    # client actually pays payment_amount * (1 - discount_percent/100).
    applied_promo: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    client: Mapped["User"] = relationship("User", foreign_keys=[client_id], back_populates="client_orders")
    operator: Mapped["User | None"] = relationship(
        "User", foreign_keys=[operator_id], back_populates="operator_orders"
    )
    files: Mapped[list["OrderFile"]] = relationship("OrderFile", back_populates="order")
    solution_files: Mapped[list["SolutionFile"]] = relationship("SolutionFile", back_populates="order")
    bids: Mapped[list["Bid"]] = relationship("Bid", back_populates="order")
    reviews: Mapped[list["Review"]] = relationship("Review", back_populates="order")
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="order")
    notes: Mapped[list["OperatorNote"]] = relationship("OperatorNote", back_populates="order")
    logs: Mapped[list["OrderLog"]] = relationship("OrderLog", back_populates="order", order_by="OrderLog.created_at")
