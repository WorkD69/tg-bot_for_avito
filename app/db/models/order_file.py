from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order


class OrderFile(Base, TimestampMixin):
    __tablename__ = "order_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    telegram_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)  # photo, document, etc.
    # Caption attached by the client when sending this file (may be None)
    caption: Mapped[str | None] = mapped_column(nullable=True)

    order: Mapped["Order"] = relationship("Order", back_populates="files")
