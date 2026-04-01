from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.client_reply import client_main_kb
from app.bot.keyboards.operator_reply import operator_dm_kb
from app.db.models.user import User, UserRole

router = Router()


def _main_kb(user: User):
    """Return the correct main keyboard for the user's role."""
    if user.role in (UserRole.operator, UserRole.admin):
        return operator_dm_kb()
    return client_main_kb()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: User):
    """Universal FSM exit — clears state and returns role-appropriate keyboard."""
    current = await state.get_state()
    if current is None:
        await message.answer("ℹ️ Нет активного действия для отмены")
        return
    await state.clear()
    await message.answer("✅ Действие отменено", reply_markup=_main_kb(user))


@router.message(CommandStart())
async def cmd_start(message: Message, user: User, state: FSMContext):
    await state.clear()  # clear any active FSM state on /start
    if user.role in (UserRole.operator, UserRole.admin):
        await message.answer(
            f"👋 Привет, {user.full_name}! Ты в режиме оператора\n\n"
            "📋 Работай с заявками через кнопки — также можешь создавать заявки как клиент",
            reply_markup=operator_dm_kb(),
        )
    else:
        await message.answer(
            f"👋 Привет, {user.full_name}! Здесь вы можете оставить заявку на решение задачи",
            reply_markup=client_main_kb(),
        )
