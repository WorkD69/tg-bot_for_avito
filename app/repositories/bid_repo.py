from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid


class BidRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, order_id: int, operator_id: int, amount: Decimal) -> None:
        """Atomic INSERT … ON CONFLICT DO UPDATE — safe against concurrent bids.

        The unique constraint uq_bid_order_operator (order_id, operator_id) ensures
        exactly one bid row per operator per order. Concurrent calls with the same
        (order_id, operator_id) will update the amount deterministically instead of
        raising IntegrityError.
        """
        stmt = (
            pg_insert(Bid)
            .values(order_id=order_id, operator_id=operator_id, amount=amount)
            .on_conflict_do_update(
                constraint="uq_bid_order_operator",
                set_={"amount": amount},
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_by_order(self, order_id: int) -> list[Bid]:
        result = await self.session.execute(
            select(Bid).where(Bid.order_id == order_id).order_by(Bid.amount)
        )
        return list(result.scalars().all())

    async def get_min_bid(self, order_id: int) -> Bid | None:
        """Returns the bid with the lowest amount.
        Tie-break rule: earliest created_at wins (first operator to place same amount).
        """
        result = await self.session.execute(
            select(Bid)
            .where(Bid.order_id == order_id)
            .order_by(Bid.amount, Bid.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_operator_bid(self, order_id: int, operator_id: int) -> Bid | None:
        result = await self.session.execute(
            select(Bid).where(Bid.order_id == order_id, Bid.operator_id == operator_id)
        )
        return result.scalar_one_or_none()

    async def get_losers(self, order_id: int, winner_operator_id: int) -> list[Bid]:
        """All bids except the winner's — to notify losing operators."""
        result = await self.session.execute(
            select(Bid)
            .where(Bid.order_id == order_id, Bid.operator_id != winner_operator_id)
        )
        return list(result.scalars().all())
