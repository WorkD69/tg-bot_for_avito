from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.bot.keyboards.client_reply import BTN_CREATE, client_main_kb
from app.bot.states.order_create import OrderCreateStates
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()

MAX_FILES = 10
MAX_COMMENT_LEN = 2000
MAX_BUDGET = Decimal("1_000_000")
MSK = timezone(timedelta(hours=3))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_budget(raw: str) -> Decimal:
    """Parse budget string → Decimal. Raises ValueError/InvalidOperation on bad input.

    Accepted:
      "1500", "1500.50", "1 500", "1,500", "1500р", "1500руб", "1500₽"
    Rejected:
      empty string, negative, zero, > MAX_BUDGET, pure text
    """
    cleaned = (
        raw.strip()
        .replace(" ", "")
        .replace(",", ".")
        .rstrip("рРрубРУБ₽")
        .strip(".")
    )
    if not cleaned:
        raise ValueError("empty")
    amount = Decimal(cleaned)
    if amount <= 0:
        raise ValueError("non-positive")
    if amount > MAX_BUDGET:
        raise ValueError("too-large")
    return amount


def _parse_deadline(raw: str):
    """Parse date string → date. Raises descriptive exceptions on bad input.

    Returns (date, None) on success.
    Raises ValueError("format") if string cannot be parsed as DD.MM.YYYY.
    Raises ValueError("past") if the date is in the past.
    """
    text = raw.strip()
    try:
        deadline = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        raise ValueError("format")
    today = datetime.now(MSK).date()
    if deadline < today:
        raise ValueError("past")
    return deadline


# ── Entry point ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CREATE, IsClient())
async def start_order(message: Message, state: FSMContext, session: AsyncSession, user: User):
    # Guard: don't interrupt an already-active FSM (e.g. file upload or comment edit)
    current = await state.get_state()
    if current is not None:
        await message.answer(
            "⚠️ Вы уже выполняете действие — завершите его или отмените через /cancel"
        )
        return

    # Guard: check active order limit before starting the FSM
    active_orders = await OrderRepo(session).get_client_active_orders(user.id)
    if len(active_orders) >= 5:
        await message.answer(
            "❌ У вас уже 5 активных заявок\n"
            "Дождитесь завершения одной из них, прежде чем создавать новую",
            reply_markup=client_main_kb(),
        )
        return
    await state.set_state(OrderCreateStates.waiting_files)
    await state.update_data(files=[])
    await message.answer(
        "📎 Отправьте файлы с заданием (до 10 штук)\n"
        "Когда закончите — отправьте /done"
    )


# ── Step 1: files ─────────────────────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_files, F.photo)
async def collect_photo(message: Message, state: FSMContext):
    await _add_file(message, state, file_id=message.photo[-1].file_id, file_type="photo")


@router.message(OrderCreateStates.waiting_files, F.document)
async def collect_document(message: Message, state: FSMContext):
    await _add_file(message, state, file_id=message.document.file_id, file_type="document")


async def _add_file(message: Message, state: FSMContext, file_id: str, file_type: str):
    data = await state.get_data()
    files: list = data.get("files", [])
    files.append({"file_id": file_id, "file_type": file_type})
    await state.update_data(files=files)
    if len(files) >= MAX_FILES:
        await message.answer(f"✅ Достигнут лимит {MAX_FILES} файлов, переходим дальше")
        await _ask_comment(message, state)
    else:
        await message.answer(f"✅ Файл принят ({len(files)}/{MAX_FILES}) — ещё или /done")


@router.message(OrderCreateStates.waiting_files, F.text == "/done")
async def files_done(message: Message, state: FSMContext):
    await _ask_comment(message, state)


@router.message(OrderCreateStates.waiting_files, F.text)
async def files_unexpected_text(message: Message):
    """User typed text instead of sending a file."""
    await message.answer(
        "📎 Отправьте файл (фото или документ) с заданием\n"
        "Когда закончите — напишите /done"
    )


@router.message(OrderCreateStates.waiting_files)
async def files_unexpected_type(message: Message):
    """User sent sticker, voice, video, etc. — unsupported at this step."""
    await message.answer(
        "⚠️ Поддерживаются только фото и документы\n"
        "Отправьте файл нужного типа или /done чтобы продолжить"
    )


async def _ask_comment(message: Message, state: FSMContext):
    await state.set_state(OrderCreateStates.waiting_comment)
    await message.answer(
        "💬 Добавьте комментарий к заданию\n"
        f"Максимум {MAX_COMMENT_LEN} символов\n"
        "Если комментария нет — отправьте «-»"
    )


# ── Step 2: comment ───────────────────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_comment, F.text)
async def got_comment(message: Message, state: FSMContext):
    raw = message.text.strip()
    if raw == "-":
        comment = None
    elif not raw:
        await message.answer(
            "⚠️ Комментарий не может быть пустым\n"
            "Напишите текст или отправьте «-» если комментария нет"
        )
        return
    elif len(raw) > MAX_COMMENT_LEN:
        await message.answer(
            f"❌ Слишком длинный комментарий ({len(raw)} символов)\n"
            f"Максимум {MAX_COMMENT_LEN} символов — сократите текст"
        )
        return
    else:
        from datetime import timezone as tz
        ts = datetime.now(tz.utc).isoformat()
        comment = f"{ts}|{raw}"
    await state.update_data(comment=comment)
    await state.set_state(OrderCreateStates.waiting_deadline)
    await message.answer(
        "📅 Укажите дедлайн в формате ДД.ММ.ГГГГ\n"
        "Например: 25.05.2026 — дата должна быть сегодня или позже"
    )


@router.message(OrderCreateStates.waiting_comment)
async def comment_unexpected_type(message: Message):
    """User sent a file or other non-text at the comment step."""
    await message.answer(
        "✏️ Введите комментарий текстом\n"
        "Если комментария нет — отправьте «-»"
    )


# ── Step 3: deadline ──────────────────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_deadline, F.text)
async def got_deadline(message: Message, state: FSMContext):
    try:
        deadline = _parse_deadline(message.text)
    except ValueError as e:
        reason = str(e)
        if reason == "format":
            await message.answer(
                "❌ Не могу распознать дату — проверьте формат\n\n"
                "Нужно: ДД.ММ.ГГГГ\n"
                "Пример: 25.05.2026"
            )
        elif reason == "past":
            await message.answer(
                "❌ Эта дата уже прошла\n"
                "Укажите сегодняшнюю дату или любую будущую в формате ДД.ММ.ГГГГ"
            )
        else:
            await message.answer("❌ Некорректная дата — введите в формате ДД.ММ.ГГГГ")
        return
    await state.update_data(deadline=deadline.isoformat())
    await state.set_state(OrderCreateStates.waiting_budget)
    await message.answer(
        "💰 Укажите желаемый бюджет в рублях\n"
        "Например: 1500 или 2000"
    )


@router.message(OrderCreateStates.waiting_deadline)
async def deadline_unexpected_type(message: Message):
    """User sent a file or other non-text at the deadline step."""
    await message.answer(
        "📅 Введите дату текстом в формате ДД.ММ.ГГГГ\n"
        "Например: 25.05.2026"
    )


# ── Step 4: budget → create order ─────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_budget, F.text)
async def got_budget(
    message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list
):
    try:
        budget = _parse_budget(message.text)
    except (InvalidOperation, ValueError) as e:
        reason = str(e)
        if reason == "too-large":
            await message.answer(
                f"❌ Слишком большая сумма — максимум {MAX_BUDGET:,.0f} ₽\n"
                "Введите реальный бюджет цифрами"
            )
        else:
            await message.answer(
                "❌ Введите сумму числом в рублях\n"
                "Например: 1500 или 2000"
            )
        return

    data = await state.get_data()

    # Limit: no more than 5 active orders per client
    active_orders = await OrderRepo(session).get_client_active_orders(user.id)
    if len(active_orders) >= 5:
        await state.clear()
        await message.answer(
            "❌ У вас уже 5 активных заявок\n"
            "Дождитесь завершения одной из них, прежде чем создавать новую",
            reply_markup=client_main_kb(),
        )
        return

    from datetime import date
    deadline = date.fromisoformat(data["deadline"]) if data.get("deadline") else None
    # UTC-aware auction end time (replaces deprecated datetime.utcnow())
    auction_end_at = datetime.now(timezone.utc) + timedelta(minutes=120)

    order_repo = OrderRepo(session)
    order = await order_repo.create(
        client_id=user.id,
        comment=data.get("comment"),
        deadline=deadline,
        budget=budget,
        auction_end_at=auction_end_at,
    )

    # Save attached files
    from app.db.models.order_file import OrderFile
    for f in data.get("files", []):
        session.add(OrderFile(order_id=order.id, telegram_file_id=f["file_id"], file_type=f["file_type"]))

    await state.clear()

    # Start auction — pass post_commit so group notify fires after commit
    from app.services.auction_service import AuctionService
    from app.bot.instance import bot
    auction = AuctionService(session=session, bot=bot, deferred=post_commit)
    await auction.start_auction(order)

    await message.answer(
        "🎉 Ваша заявка создана! Ожидайте, пока операторы возьмут её в работу\n\n"
        "📋 Статус заявки вы можете посмотреть в разделе «Текущие заявки»",
        reply_markup=client_main_kb(),
    )


@router.message(OrderCreateStates.waiting_budget)
async def budget_unexpected_type(message: Message):
    """User sent a file or other non-text at the budget step."""
    await message.answer(
        "💰 Введите бюджет числом в рублях\n"
        "Например: 1500"
    )
