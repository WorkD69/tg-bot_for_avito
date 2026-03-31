from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bid import Bid


class BidRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, order_id: int, operator_id: int, amount: Decimal) -> Bid:
        bid = Bid(order_id=order_id, operator_id=operator_id, amount=amount)
        self.session.add(bid)
        await self.session.flush()
        return bid

    async def get_by_order(self, order_id: int) -> list[Bid]:
        result = await self.session.execute(
            select(Bid).where(Bid.order_id == order_id).order_by(Bid.amount)
        )
        return list(result.scalars().all())

    async def get_min_bid(self, order_id: int) -> Bid | None:
        """Returns the bid with the lowest amount; ties broken by earliest created_at."""
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
