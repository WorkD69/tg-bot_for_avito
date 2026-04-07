import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.user import User


class OrderLogAction(str, enum.Enum):
    created = "created"
    bid_placed = "bid_placed"
    auction_closed = "auction_closed"
    operator_assigned = "operator_assigned"
    payment_received = "payment_received"
    payment_confirmed = "payment_confirmed"
    price_updated = "price_updated"
    requisites_sent = "requisites_sent"
    solution_uploaded = "solution_uploaded"
    completed = "completed"
    cancelled = "cancelled"
    comment_added = "comment_added"
    files_added = "files_added"
    dispute_opened = "dispute_opened"


class OrderLog(Base, TimestampMixin):
    __tablename__ = "order_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[OrderLogAction] = mapped_column(
        Enum(OrderLogAction, name="orderlogaction"), nullable=False
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="logs")
    actor: Mapped["User | None"] = relationship("User", back_populates="order_logs")
