from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.bid import Bid
from app.db.models.message import Message
from app.db.models.order import Order, OrderStatus


def _with_relations():
    return (
        selectinload(Order.client),
        selectinload(Order.operator),
        selectinload(Order.files),
        selectinload(Order.solution_files),
        selectinload(Order.bids).selectinload(Bid.operator),
        selectinload(Order.messages).selectinload(Message.sender),
        selectinload(Order.notes),
        selectinload(Order.reviews),
    )


class OrderRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        client_id: int,
        comment: str | None,
        deadline: date | None,
        budget: Decimal | None,
        auction_end_at: datetime,
    ) -> Order:
        order = Order(
            client_id=client_id,
            comment=comment,
            deadline=deadline,
            budget=budget,
            auction_end_at=auction_end_at,
            status=OrderStatus.pending,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_by_id(self, order_id: int, load_relations: bool = False) -> Order | None:
        if not load_relations:
            return await self.session.get(Order, order_id)
        result = await self.session.execute(
            select(Order).where(Order.id == order_id).options(*_with_relations())
        )
        return result.scalar_one_or_none()

    async def get_by_invoice_id(self, invoice_id: str) -> Order | None:
        result = await self.session.execute(
            select(Order).where(Order.payment_invoice_id == invoice_id)
        )
        return result.scalar_one_or_none()

    async def get_free_orders(self) -> list[Order]:
        """Orders in auction: status=pending, no operator assigned."""
        result = await self.session.execute(
            select(Order)
            .where(Order.status == OrderStatus.pending, Order.operator_id.is_(None))
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_operator_active_orders(self, operator_id: int) -> list[Order]:
        """Operator's current (non-final) orders."""
        final = (OrderStatus.completed, OrderStatus.cancelled)
        result = await self.session.execute(
            select(Order)
            .where(Order.operator_id == operator_id, Order.status.not_in(final))
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_operator_completed_orders(self, operator_id: int) -> list[Order]:
        result = await self.session.execute(
            select(Order)
            .where(Order.operator_id == operator_id, Order.status == OrderStatus.completed)
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_client_active_orders(self, client_id: int) -> list[Order]:
        final = (OrderStatus.completed, OrderStatus.cancelled)
        result = await self.session.execute(
            select(Order)
            .where(Order.client_id == client_id, Order.status.not_in(final))
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_client_history(self, client_id: int) -> list[Order]:
        final = (OrderStatus.completed, OrderStatus.cancelled)
        result = await self.session.execute(
            select(Order)
            .where(Order.client_id == client_id, Order.status.in_(final))
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_stats(self) -> dict[str, int]:
        from sqlalchemy import func
        result = await self.session.execute(
            select(Order.status, func.count(Order.id)).group_by(Order.status)
        )
        return {str(status.value): count for status, count in result.all()}

    async def update_status(self, order: Order, status: OrderStatus) -> None:
        order.status = status
        order.updated_at = datetime.utcnow()
        await self.session.flush()

    async def assign_operator(
        self, order: Order, operator_id: int, payment_amount: Decimal
    ) -> None:
        order.operator_id = operator_id
        order.payment_amount = payment_amount
        order.payment_invoice_id = str(order.id)
        order.status = OrderStatus.awaiting_payment
        order.updated_at = datetime.utcnow()
        await self.session.flush()

    async def set_group_message_id(self, order: Order, message_id: int) -> None:
        order.group_message_id = message_id
        await self.session.flush()
