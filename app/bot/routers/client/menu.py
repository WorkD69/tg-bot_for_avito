from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.client_reply import client_main_kb
from app.db.models.user import User, UserRole

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, user: User):
    if user.role in (UserRole.operator, UserRole.admin):
        from app.bot.keyboards.operator_reply import operator_group_kb
        await message.answer(
            f"👋 Привет, {user.full_name}! Ты в режиме оператора\n\n"
            "📋 Также можешь создавать заявки как клиент — используй кнопки ниже",
            reply_markup=client_main_kb(),
        )
    else:
        await message.answer(
            f"👋 Привет, {user.full_name}! Здесь вы можете оставить заявку на решение задачи",
            reply_markup=client_main_kb(),
        )
