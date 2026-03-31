from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.client_reply import client_main_kb
from app.db.models.user import User, UserRole

router = Router()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Universal FSM exit — works for all roles."""
    current = await state.get_state()
    if current is None:
        await message.answer("ℹ️ Нет активного действия для отмены")
        return
    await state.clear()
    await message.answer("✅ Действие отменено", reply_markup=client_main_kb())


@router.message(CommandStart())
async def cmd_start(message: Message, user: User, state: FSMContext):
    await state.clear()  # clear any active FSM state on /start
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
