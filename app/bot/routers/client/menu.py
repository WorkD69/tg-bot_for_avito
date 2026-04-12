from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.keyboards.client_reply import BTN_CREATE, client_main_kb
from app.bot.keyboards.operator_reply import operator_dm_kb
from app.bot.middlewares.user_register import _parse_start_payload
from app.db.models.user import User, UserRole

router = Router()

# ── Welcome text by source ────────────────────────────────────────────────────
# Shown on /start when a deeplink payload with a known source is present.
# Also shown on first /start for new users (is_new_user=True) even without payload.
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


def _extract_payload_source(message: Message) -> str | None:
    """Parse /start deeplink payload and return source if it's a known marketing source.

    Returns None for plain /start, unknown, or direct sources.
    """
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0] == "/start":
        src, _ = _parse_start_payload(parts[1].strip().lower())
        if src not in ("unknown", "direct"):
            return src
    return None


def _welcome_text(user: User, is_new_user: bool, payload_source: str | None = None) -> str:
    """Return appropriate welcome text.

    Priority:
    1. payload_source — deeplink source from current /start (avito, tg_channel, etc.)
    2. user.source — stored source, used only on first visit (is_new_user=True)
    3. Default — plain welcome without source mention
    """
    source = payload_source or (user.source if is_new_user else None)
    if source:
        template = _WELCOME_BY_SOURCE.get(source, _WELCOME_DEFAULT)
        return template.format(name=user.full_name, btn=BTN_CREATE)
    return _WELCOME_DEFAULT.format(name=user.full_name)


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

    # Detect marketing source from current deeplink payload (works for all users, incl. existing)
    payload_source = _extract_payload_source(message)

    if user.role in (UserRole.operator, UserRole.admin):
        source_hint = f"\n\n🔗 Источник перехода: {payload_source}" if payload_source else ""
        await message.answer(
            f"👋 Привет, {user.full_name}! Ты в режиме оператора\n\n"
            f"📋 Работай с заявками через кнопки — также можешь создавать заявки как клиент"
            f"{source_hint}",
            reply_markup=operator_dm_kb(),
        )
    else:
        await message.answer(
            _welcome_text(user, is_new_user, payload_source=payload_source),
            reply_markup=client_main_kb(),
        )
