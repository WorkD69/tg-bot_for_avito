import enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Known attribution sources — written from /start deeplink payload
KNOWN_SOURCES = frozenset({"avito", "tg_channel", "direct"})

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.bid import Bid
    from app.db.models.review import Review
    from app.db.models.message import Message
    from app.db.models.operator_note import OperatorNote
    from app.db.models.order_log import OrderLog


class UserRole(str, enum.Enum):
    client = "client"
    operator = "operator"
    admin = "admin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="userrole"), nullable=False, default=UserRole.client
    )
    # Attribution: how the user first arrived. Set once at registration from /start deeplink.
    # 'avito' | 'tg_channel' | 'direct' | 'unknown'
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")

    client_orders: Mapped[list["Order"]] = relationship(
        "Order", foreign_keys="Order.client_id", back_populates="client"
    )
    operator_orders: Mapped[list["Order"]] = relationship(
        "Order", foreign_keys="Order.operator_id", back_populates="operator"
    )
    bids: Mapped[list["Bid"]] = relationship("Bid", back_populates="operator")
    reviews: Mapped[list["Review"]] = relationship("Review", foreign_keys="Review.client_id", back_populates="client")
    sent_messages: Mapped[list["Message"]] = relationship("Message", back_populates="sender")
    notes: Mapped[list["OperatorNote"]] = relationship("OperatorNote", back_populates="operator")
    order_logs: Mapped[list["OrderLog"]] = relationship("OrderLog", back_populates="actor")
