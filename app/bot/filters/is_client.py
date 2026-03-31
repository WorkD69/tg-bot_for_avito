from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

from app.db.models.user import User, UserRole


class IsClient(BaseFilter):
    """Passes for all registered users. Operators and admins can use client UI too."""
    async def __call__(self, event: Message | CallbackQuery, user: User | None = None) -> bool:
        if user is None:
            return False
        return user.role in (UserRole.client, UserRole.operator, UserRole.admin)
