from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User, UserRole


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def create(self, telegram_id: int, full_name: str, username: str | None) -> User:
        user = User(telegram_id=telegram_id, full_name=full_name, username=username)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_role(self, role: UserRole) -> list[User]:
        result = await self.session.execute(
            select(User).where(User.role == role).order_by(User.full_name)
        )
        return list(result.scalars().all())

    async def set_role(self, user: User, role: UserRole) -> None:
        user.role = role
        await self.session.flush()
