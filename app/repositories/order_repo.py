from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.bid import Bid
from app.db.models.message import Message
from app.db.models.order import Order, OrderStatus
from app.db.models.order_log import OrderLog, OrderLogAction


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
        selectinload(Order.logs),
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
        await self.add_log(order_id=order.id, actor_id=client_id, action=OrderLogAction.created)
        return order

    async def get_by_id(self, order_id: int, load_relations: bool = False) -> Order | None:
        if not load_relations:
            return await self.session.get(Order, order_id)
        result = await self.session.execute(
            select(Order).where(Order.id == order_id).options(*_with_relations())
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, order_id: int) -> Order | None:
        """Lock the row for transactional operations (auction close, payment confirm)."""
        result = await self.session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
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

    async def get_unconfirmed_payments(self) -> list[Order]:
        """Orders where payment was received (payment_received_at set) but admin has not
        confirmed yet (payment_confirmed_at is null). Used for daily reminder to admin."""
        result = await self.session.execute(
            select(Order).where(
                Order.payment_received_at.isnot(None),
                Order.payment_confirmed_at.is_(None),
                Order.status == OrderStatus.awaiting_payment,
            ).order_by(Order.payment_received_at)
        )
        return list(result.scalars().all())

    async def get_overdue_pending(self) -> list[Order]:
        """Orders in pending status past their auction_end_at (missed scheduler job)."""
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(Order).where(
                Order.status == OrderStatus.pending,
                Order.auction_end_at.isnot(None),
                Order.auction_end_at < now,
            )
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
        order.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def cancel(self, order: Order, cancelled_by: str) -> None:
        """Atomically transition to cancelled: sets status + cancelled_by in one flush.

        Invariant: cancelled.cancelled_by must be non-null.
        Using this method instead of update_status ensures the invariant is never violated.
        """
        order.status = OrderStatus.cancelled
        order.cancelled_by = cancelled_by
        order.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def confirm_payment(self, order: Order) -> None:
        """Atomically transition awaiting_payment → in_progress.

        Caller MUST ensure payment_received_at is already set (guard in cmd_confirm_payment).
        Invariants enforced:
          - in_progress.payment_confirmed_at is set to now
          - in_progress.cancelled_by must be null (preserved — not touched here)
        """
        now = datetime.now(timezone.utc)
        order.payment_confirmed_at = now
        order.status = OrderStatus.in_progress
        order.updated_at = now
        await self.session.flush()

    async def assign_operator(
        self, order: Order, operator_id: int, payment_amount: Decimal
    ) -> None:
        order.operator_id = operator_id
        order.payment_amount = payment_amount
        order.payment_revision = (order.payment_revision or 0) + 1
        # Invoice ID is versioned: "{order_id}_{revision}" — old revision IDs become stale
        order.payment_invoice_id = f"{order.id}_{order.payment_revision}"
        order.status = OrderStatus.awaiting_payment
        order.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def update_agreed_price(self, order: Order, new_amount: Decimal) -> None:
        """Agree on a new price after negotiation.

        Called when BOTH parties agree (operator accepts client counter, or operator sets counter).
        Resets the entire payment state so the new revision must be paid fresh.
        Increments payment_revision → old Robokassa invoice IDs become stale (not found in DB).
        """
        order.payment_amount = new_amount
        order.payment_revision = (order.payment_revision or 0) + 1
        # Versioned invoice ID — old Robokassa callbacks will get "not found"
        order.payment_invoice_id = f"{order.id}_{order.payment_revision}"
        # Reset payment state: old payment receipt / confirmation no longer valid for new price
        order.payment_received_at = None
        order.payment_confirmed_at = None
        order.updated_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def add_log(
        self,
        order_id: int,
        action: OrderLogAction,
        actor_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        log = OrderLog(order_id=order_id, actor_id=actor_id, action=action, detail=detail)
        self.session.add(log)
        await self.session.flush()
