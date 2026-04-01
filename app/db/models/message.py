import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.user import User


class MessageDirection(str, enum.Enum):
    # .name must match DB enum labels created in migration 0001
    client_to_operator = "client_to_operator"
    operator_to_client = "operator_to_client"


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, name="messagedirection"), nullable=False
    )

    order: Mapped["Order"] = relationship("Order", back_populates="messages")
    sender: Mapped["User"] = relationship("User", back_populates="sent_messages")
