from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery

from app.config import settings


class IsOperatorGroup(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if isinstance(event, Message):
            return event.chat.id == settings.operator_group_id
        if isinstance(event, CallbackQuery) and event.message:
            return event.message.chat.id == settings.operator_group_id
        return False
