from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.client_reply import (
    BTN_CREATE,
    BTN_CURRENT,
    BTN_HISTORY,
    BTN_HOW,
    BTN_REVIEWS,
    client_main_kb,
)
from app.bot.keyboards.operator_reply import operator_dm_kb
from app.bot.filters import IsClient
from app.bot.middlewares.user_register import _parse_start_payload
from app.db.models.user import User, UserRole

router = Router()

# ── Welcome text by source ────────────────────────────────────────────────────
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
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0] == "/start":
        src, _ = _parse_start_payload(parts[1].strip().lower())
        if src not in ("unknown", "direct"):
            return src
    return None


def _welcome_text(user: User, is_new_user: bool, payload_source: str | None = None) -> str:
    source = payload_source or (user.source if is_new_user else None)
    if source:
        template = _WELCOME_BY_SOURCE.get(source, _WELCOME_DEFAULT)
        return template.format(name=user.full_name, btn=BTN_CREATE)
    return _WELCOME_DEFAULT.format(name=user.full_name)


def _main_kb(user: User):
    if user.role in (UserRole.operator, UserRole.admin):
        return operator_dm_kb()
    return client_main_kb()


def _how_it_works_text() -> str:
    from app.config import settings
    return (
        "ℹ️ Как мы работаем:\n\n"
        "1️⃣ Вы создаёте заявку — описание задачи, дедлайн, желаемый бюджет\n"
        "2️⃣ Операторы делают ставки — вы получаете лучшую цену\n"
        "3️⃣ Вы оплачиваете удобным способом\n"
        "4️⃣ Оператор берёт задачу в работу\n"
        "5️⃣ Готовое решение приходит прямо сюда\n\n"
        "💬 Есть вопросы по результату — напишите оператору прямо в чате заявки\n"
        "⚠️ Не устраивает результат — нажмите «Оспорить», разберёмся\n\n"
        f"❓ Остались вопросы — мы на связи: {settings.support_handle}"
    )


# ── MAIN MENU BUTTONS — registered first so they always win over FSM handlers ──
#
# All five handlers call state.clear() unconditionally — this is a no-op when no
# FSM is active, so it's always safe. No "action cancelled" message is shown to
# avoid polluting the chat; the transition to the new section is enough feedback.

@router.message(F.text == BTN_HOW, IsClient())
async def how_it_works(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(_how_it_works_text())


@router.message(F.text == BTN_CREATE, IsClient())
async def menu_create_order(message: Message, state: FSMContext, session: AsyncSession, user: User):
    """'Создать заявку' always starts (or restarts) order creation.

    If FSM is already active it is cleared silently — the user pressed the button
    intentionally, so restarting is the correct action. No blocking message.
    """
    await state.clear()

    from app.repositories.order_repo import OrderRepo
    active_orders = await OrderRepo(session).get_client_active_orders(user.id)
    if len(active_orders) >= 5:
        await message.answer(
            "❌ У вас уже 5 активных заявок\n"
            "Дождитесь завершения одной из них, прежде чем создавать новую",
            reply_markup=client_main_kb(),
        )
        return

    from app.bot.states.order_create import OrderCreateStates
    await state.set_state(OrderCreateStates.waiting_files)
    await state.update_data(files=[])
    await message.answer(
        "📎 Отправьте файлы с заданием (до 10 штук)\n"
        "Когда закончите — отправьте /done\n"
        "Для отмены — /cancel"
    )


@router.message(F.text == BTN_CURRENT, IsClient())
async def menu_current_orders(message: Message, state: FSMContext, session: AsyncSession, user: User):
    """'Текущие заявки' — always works, auto-clears active FSM."""
    await state.clear()

    from app.repositories.order_repo import OrderRepo
    from app.bot.keyboards.order_inline import client_orders_list_kb
    orders = await OrderRepo(session).get_client_active_orders(user.id)
    if not orders:
        await message.answer("📭 У вас нет активных заявок")
        return
    await message.answer("📋 Ваши текущие заявки:", reply_markup=client_orders_list_kb(orders))


@router.message(F.text == BTN_HISTORY, IsClient())
async def menu_history_orders(message: Message, state: FSMContext, session: AsyncSession, user: User):
    """'История заявок' — always works, auto-clears active FSM."""
    await state.clear()

    from app.repositories.order_repo import OrderRepo
    from app.bot.keyboards.order_inline import client_orders_list_kb
    orders = await OrderRepo(session).get_client_history(user.id)
    if not orders:
        await message.answer("📭 История заявок пуста")
        return
    await message.answer("🗂 История заявок:", reply_markup=client_orders_list_kb(orders))


@router.message(F.text == BTN_REVIEWS, IsClient())
async def menu_reviews(message: Message, state: FSMContext):
    """'Отзывы' — always works, auto-clears active FSM."""
    await state.clear()

    from app.bot.keyboards.admin_inline import reviews_menu_kb
    await message.answer("⭐ Отзывы:", reply_markup=reviews_menu_kb())


# ── /cancel ───────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user: User):
    """Universal FSM exit — clears state and returns role-appropriate keyboard."""
    current = await state.get_state()
    if current is None:
        await message.answer("ℹ️ Нет активного действия для отмены")
        return
    await state.clear()
    await message.answer("✅ Действие отменено", reply_markup=_main_kb(user))


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, user: User, state: FSMContext, is_new_user: bool = False):
    current = await state.get_state()
    await state.clear()
    if current is not None:
        await message.answer(
            "ℹ️ Незавершённое действие отменено\n"
            "Используйте /cancel в любое время чтобы прервать текущее действие"
        )

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
