from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

from app.db.models.user import User, UserRole


class IsAdmin(BaseFilter):
    """Passes only for users with role=admin in the database.

    Bootstrap note: UserRegisterMiddleware auto-promotes the configured
    admin_telegram_id to admin role on first interaction, so this filter
    is always correct as long as the middleware runs before filters.
    """
    async def __call__(self, event: Message | CallbackQuery, user: User | None = None) -> bool:
        if user is None:
            return False
        return user.role == UserRole.admin
