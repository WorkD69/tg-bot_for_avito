from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.promo_code import PromoCode


class PromoRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active(self, code: str) -> PromoCode | None:
        """Return active promo by code (case-insensitive). None if not found or inactive."""
        result = await self.session.execute(
            select(PromoCode).where(
                PromoCode.code == code.upper().strip(),
                PromoCode.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()
