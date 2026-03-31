from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.review import Review


class ReviewRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, order_id: int, client_id: int, text: str, rating: int = 5) -> Review:
        review = Review(order_id=order_id, client_id=client_id, text=text, rating=rating)
        self.session.add(review)
        await self.session.flush()
        return review

    async def get_approved(self) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.is_approved.is_(True))
            .options(selectinload(Review.client))
            .order_by(Review.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_client(self, client_id: int) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.client_id == client_id)
            .order_by(Review.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_pending(self) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.is_approved.is_(False))
            .options(selectinload(Review.client))
            .order_by(Review.created_at)
        )
        return list(result.scalars().all())

    async def get_by_id(self, review_id: int) -> Review | None:
        return await self.session.get(Review, review_id)

    async def approve(self, review: Review) -> None:
        review.is_approved = True
        await self.session.flush()

    async def delete(self, review: Review) -> None:
        await self.session.delete(review)
        await self.session.flush()
