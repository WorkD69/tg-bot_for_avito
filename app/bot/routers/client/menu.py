from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.client_reply import BTN_CREATE, client_main_kb
from app.bot.keyboards.operator_reply import operator_dm_kb
from app.db.models.user import User, UserRole

router = Router()

# ── Welcome text by source ────────────────────────────────────────────────────
# Only shown once — on first /start (is_new_user=True).
# Keyed by users.source value. Falls back to _WELCOME_DEFAULT for unknown/direct.
# To add a new source: add an entry here and in KNOWN_SOURCES in user.py.
_WELCOME_BY_SOURCE: dict[str, str] = {
    "avito": (
        "👋 Привет, {name}! Нашли нас на Авито — отлично\n\n"
        "Здесь можно оставить любую задачу и получить готовое решение. "
        'Нажмите «{btn}» чтобы начать'
    ),
    "tg_channel": (
        "👋 Привет, {name}! Рады видеть вас из нашего канала\n\n"
        'Нажмите «{btn}» чтобы оставить задачу — найдём исполнителя быстро'
    ),
}
_WELCOME_DEFAULT = "👋 Привет, {name}! Здесь вы можете оставить заявку на решение задачи"


def _welcome_text(user: User, is_new_user: bool) -> str:
    """Return appropriate welcome text based on whether user is new and their source."""
    if not is_new_user:
        return _WELCOME_DEFAULT.format(name=user.full_name)
    template = _WELCOME_BY_SOURCE.get(user.source, _WELCOME_DEFAULT)
    return template.format(name=user.full_name, btn=BTN_CREATE)


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
async def cmd_start(message: Message, user: User, state: FSMContext, is_new_user: bool = False):
    current = await state.get_state()
    await state.clear()  # clear any active FSM state on /start
    if current is not None:
        await message.answer(
            "ℹ️ Незавершённое действие отменено\n"
            "Используйте /cancel в любое время чтобы прервать текущее действие"
        )
    if user.role in (UserRole.operator, UserRole.admin):
        await message.answer(
            f"👋 Привет, {user.full_name}! Ты в режиме оператора\n\n"
            "📋 Работай с заявками через кнопки — также можешь создавать заявки как клиент",
            reply_markup=operator_dm_kb(),
        )
    else:
        await message.answer(
            _welcome_text(user, is_new_user),
            reply_markup=client_main_kb(),
        )
