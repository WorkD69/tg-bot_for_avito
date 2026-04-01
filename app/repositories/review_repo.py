from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.review import Review, ReviewStatus


class ReviewRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, order_id: int, client_id: int, text: str, rating: int = 5
    ) -> tuple[Review, bool]:
        """Idempotent create.

        Returns (review, is_new):
          - is_new=True  → review was just created; caller should send admin notification
          - is_new=False → review already existed; caller must NOT send another admin alert
        """
        existing = await self.get_by_order_client(order_id, client_id)
        if existing:
            return existing, False
        review = Review(
            order_id=order_id,
            client_id=client_id,
            text=text,
            rating=rating,
            status=ReviewStatus.pending,
        )
        self.session.add(review)
        await self.session.flush()
        return review, True

    async def get_approved(self) -> list[Review]:
        result = await self.session.execute(
            select(Review)
            .where(Review.status == ReviewStatus.approved)
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
            .where(Review.status == ReviewStatus.pending)
            .options(selectinload(Review.client))
            .order_by(Review.created_at)
        )
        return list(result.scalars().all())

    async def get_by_id(self, review_id: int) -> Review | None:
        return await self.session.get(Review, review_id)

    async def get_by_order_client(self, order_id: int, client_id: int) -> Review | None:
        result = await self.session.execute(
            select(Review).where(Review.order_id == order_id, Review.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def set_status(
        self,
        review: Review,
        status: ReviewStatus,
        moderator_id: int | None = None,
    ) -> None:
        review.status = status
        review.moderated_by = moderator_id
        review.moderated_at = datetime.now(timezone.utc)
        await self.session.flush()

    # Legacy aliases kept for backward compat
    async def approve(self, review: Review) -> None:
        await self.set_status(review, ReviewStatus.approved)

    async def delete(self, review: Review) -> None:
        """Soft-delete: set status=rejected."""
        await self.set_status(review, ReviewStatus.rejected)
