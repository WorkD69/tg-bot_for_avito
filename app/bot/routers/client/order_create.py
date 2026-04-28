from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.bot.keyboards.callbacks import OrderCB
from app.bot.keyboards.client_reply import client_main_kb
from app.bot.states.order_create import OrderCreateStates
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()

MAX_FILES = 10
MAX_COMMENT_LEN = 2000
MAX_BUDGET = Decimal("1_000_000")
MSK = timezone(timedelta(hours=3))


# ── Helpers ───────────────────────────────────────────────────────────────────

import re as _re

# Matches exactly DD.MM.YYYY — two digits, dot, two digits, dot, four digits
_DATE_RE = _re.compile(r"^\d{2}\.\d{2}\.\d{4}$")

# Matches a suspicious "thousands separator" pattern: 1–3 digits, separator, exactly 3 digits, end
# e.g. "1,500" or "1.500" — ambiguous between "1500" and "1.5"
_AMBIGUOUS_RE = _re.compile(r"^\d{1,3}[.,]\d{3}$")


def _parse_budget(raw: str) -> Decimal:
    """Parse budget string → Decimal. Raises ValueError/InvalidOperation on bad input.

    Accepted:
      "1500", "1500.50", "1 500", "1500р", "1500руб", "1500₽", "1500,50"
    Rejected:
      "1,500" / "1.500" — ambiguous (thousands vs decimal?)
      empty string, negative, zero, > MAX_BUDGET, pure text
    """
    # Strip currency suffixes and whitespace first
    cleaned = (
        raw.strip()
        .rstrip("рРрубРУБ₽")
        .strip()
    )
    if not cleaned:
        raise ValueError("empty")

    # Remove spaces used as thousands separator (e.g. "1 500" → "1500")
    no_spaces = cleaned.replace(" ", "")

    # Reject ambiguous patterns: "1,500" or "1.500" (1–3 digits + separator + 3 digits)
    if _AMBIGUOUS_RE.match(no_spaces):
        raise ValueError("ambiguous")

    # Now safe to convert comma → decimal point
    normalized = no_spaces.replace(",", ".")

    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        raise ValueError("invalid")

    if amount <= 0:
        raise ValueError("non-positive")
    if amount > MAX_BUDGET:
        raise ValueError("too-large")
    return amount


def _parse_deadline(raw: str):
    """Parse date string → date. Raises descriptive exceptions on bad input.

    Raises ValueError("format") if string doesn't match DD.MM.YYYY pattern.
    Raises ValueError("nonexistent") if the date pattern is right but date doesn't exist.
    Raises ValueError("past") if the date is valid but in the past.
    """
    text = raw.strip()

    # Step 1: structural format check — must be exactly DD.MM.YYYY
    if not _DATE_RE.match(text):
        raise ValueError("format")

    # Step 2: logical existence check — 32.01.2026, 31.02.2026, etc.
    try:
        deadline = datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        raise ValueError("nonexistent")

    # Step 3: past check
    today = datetime.now(MSK).date()
    if deadline < today:
        raise ValueError("past")

    return deadline


# ── "Создать ещё заявку" from completed order card ───────────────────────────

@router.callback_query(OrderCB.filter(F.action == "new_order"), IsClient())
async def new_order_from_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    """Inline button on completed order card — starts a new order FSM."""
    current = await state.get_state()
    if current is not None:
        await callback.answer(
            "⚠️ Вы уже выполняете другое действие — завершите его или отмените через /cancel",
            show_alert=True,
        )
        return
    active_orders = await OrderRepo(session).get_client_active_orders(user.id)
    if len(active_orders) >= 5:
        await callback.answer(
            "❌ У вас уже 5 активных заявок — дождитесь завершения одной из них",
            show_alert=True,
        )
        return
    await state.set_state(OrderCreateStates.waiting_files)
    await state.update_data(files=[])
    await callback.message.answer(
        "📎 Отправьте файлы с заданием (до 10 штук)\n"
        "Когда закончите — отправьте /done\n"
        "Для отмены — /cancel"
    )
    await callback.answer()


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
    # Capture caption so operators see any note attached to the file
    caption = message.caption or None
    files.append({"file_id": file_id, "file_type": file_type, "caption": caption})
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
        "💬 Добавьте комментарий к заданию\n\n"
        "Напишите что важно знать оператору: особые требования, формат ответа, "
        "с какого места непонятно — всё это поможет получить точный результат\n\n"
        "Если комментария нет — отправьте «-»\n"
        "Для отмены — /cancel"
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
        "Например: 25.05.2026 — дата должна быть сегодня или позже\n"
        "Для отмены — /cancel"
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
                "❌ Неверный формат даты\n\n"
                "Нужно: ДД.ММ.ГГГГ\n"
                "Пример: 25.05.2026"
            )
        elif reason == "nonexistent":
            await message.answer(
                "❌ Такой даты не существует — проверьте день и месяц\n"
                "Пример корректной даты: 25.05.2026"
            )
        elif reason == "past":
            await message.answer(
                "❌ Эта дата уже прошла\n"
                "Укажите сегодняшнюю или будущую дату в формате ДД.ММ.ГГГГ"
            )
        else:
            await message.answer("❌ Некорректная дата — введите в формате ДД.ММ.ГГГГ")
        return
    await state.update_data(deadline=deadline.isoformat())
    await state.set_state(OrderCreateStates.waiting_budget)
    await message.answer(
        "💰 Укажите желаемый бюджет в рублях\n"
        "Например: 1500 или 2000\n\n"
        "Для отмены — /cancel"
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
        elif reason == "ambiguous":
            await message.answer(
                "❌ Неоднозначный формат суммы\n"
                "Используйте: 1500 или 1500,50 (не 1,500 или 1.500)"
            )
        else:
            await message.answer(
                "❌ Введите сумму числом в рублях\n"
                "Например: 1500 или 1500,50"
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

    await state.update_data(budget=str(budget))
    await state.set_state(OrderCreateStates.waiting_promo)
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить →")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "🎟 Есть промокод? Введите его или нажмите «Пропустить»\n"
        "Для отмены — /cancel",
        reply_markup=skip_kb,
    )


@router.message(OrderCreateStates.waiting_promo, F.text)
async def got_promo(
    message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list
):
    raw = message.text.strip()
    promo_code: str | None = None
    discount_percent = None

    if raw != "Пропустить →":
        from app.repositories.promo_repo import PromoRepo
        promo = await PromoRepo(session).get_active(raw)
        if promo is None:
            await message.answer(
                "❌ Промокод не найден или недействителен\n"
                "Введите другой промокод или нажмите «Пропустить»"
            )
            return

        if promo.first_order_only:
            # Check if client has any previously paid orders
            from sqlalchemy import select, func
            from app.db.models.order import Order as OrderModel, OrderStatus
            result = await session.execute(
                select(func.count()).where(
                    OrderModel.client_id == user.id,
                    OrderModel.payment_received_at.is_not(None),
                )
            )
            paid_count = result.scalar_one()
            if paid_count > 0:
                await message.answer(
                    "❌ Этот промокод действует только на первый заказ\n"
                    "Нажмите «Пропустить» чтобы продолжить без скидки"
                )
                return

        promo_code = promo.code
        discount_percent = promo.discount_percent
        await message.answer(
            f"✅ Промокод применён — скидка {discount_percent:.0f}%"
        )

    await _create_order(message, state, session, user, post_commit, promo_code, discount_percent)


@router.message(OrderCreateStates.waiting_promo)
async def promo_unexpected_type(message: Message):
    await message.answer(
        "🎟 Введите промокод текстом или нажмите «Пропустить»"
    )


async def _create_order(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    post_commit: list,
    applied_promo: str | None,
    discount_percent,
):
    from datetime import date
    data = await state.get_data()
    deadline = date.fromisoformat(data["deadline"]) if data.get("deadline") else None
    budget = Decimal(data["budget"])
    auction_end_at = datetime.now(timezone.utc) + timedelta(minutes=120)

    order_repo = OrderRepo(session)
    order = await order_repo.create(
        client_id=user.id,
        comment=data.get("comment"),
        deadline=deadline,
        budget=budget,
        auction_end_at=auction_end_at,
        applied_promo=applied_promo,
        discount_percent=discount_percent,
    )

    # Save attached files (with caption if operator provided one inline)
    from app.db.models.order_file import OrderFile
    for f in data.get("files", []):
        session.add(OrderFile(
            order_id=order.id,
            telegram_file_id=f["file_id"],
            file_type=f["file_type"],
            caption=f.get("caption") or None,
        ))

    await state.clear()

    # Start auction — pass post_commit so group notify fires after commit
    from app.services.auction_service import AuctionService
    from app.bot.instance import bot
    auction = AuctionService(session=session, bot=bot, deferred=post_commit)
    await auction.start_auction(order)

    await message.answer(
        "🎉 Заявка принята!\n\n"
        "👀 Операторы уже видят её и подбирают цену — как только кто-то откликнется, "
        "вы сразу получите уведомление\n\n"
        "📋 Текущий статус — в разделе «Текущие заявки»",
        reply_markup=client_main_kb(),
    )


@router.message(OrderCreateStates.waiting_budget)
async def budget_unexpected_type(message: Message):
    """User sent a file or other non-text at the budget step."""
    await message.answer(
        "💰 Введите бюджет числом в рублях\n"
        "Например: 1500"
    )
