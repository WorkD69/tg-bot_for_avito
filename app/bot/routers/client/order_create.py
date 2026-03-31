from datetime import datetime, timedelta, timezone

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
MSK = timezone(timedelta(hours=3))


# ── Entry point ──────────────────────────────────────────────────────────────

@router.message(F.text == BTN_CREATE, IsClient())
async def start_order(message: Message, state: FSMContext):
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


async def _ask_comment(message: Message, state: FSMContext):
    await state.set_state(OrderCreateStates.waiting_comment)
    await message.answer("💬 Добавьте комментарий к заданию (или отправьте «-» если нет):")


# ── Step 2: comment ───────────────────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_comment, F.text)
async def got_comment(message: Message, state: FSMContext):
    from datetime import datetime, timezone as tz
    raw = message.text.strip()
    if raw == "-":
        comment = None
    else:
        ts = datetime.now(tz.utc).isoformat()
        comment = f"{ts}|{raw}"
    await state.update_data(comment=comment)
    await state.set_state(OrderCreateStates.waiting_deadline)
    await message.answer("📅 Укажите дедлайн в формате ДД.ММ.ГГГГ:")


# ── Step 3: deadline ──────────────────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_deadline, F.text)
async def got_deadline(message: Message, state: FSMContext):
    try:
        deadline = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer(
            "❌ Некорректный формат даты\n"
            "Введите дату в формате ДД.ММ.ГГГГ, не предшествующую текущей"
        )
        return
    today = datetime.now(MSK).date()
    if deadline < today:
        await message.answer(
            "❌ Некорректный формат даты\n"
            "Введите дату в формате ДД.ММ.ГГГГ, не предшествующую текущей"
        )
        return
    await state.update_data(deadline=deadline.isoformat())
    await state.set_state(OrderCreateStates.waiting_budget)
    await message.answer("💰 Укажите желаемый бюджет (число в рублях):")


# ── Step 4: budget → create order ─────────────────────────────────────────────

@router.message(OrderCreateStates.waiting_budget, F.text)
async def got_budget(message: Message, state: FSMContext, session: AsyncSession, user: User):
    try:
        budget = float(message.text.strip().replace(",", "."))
        if budget <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное число, например 1500:")
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
    auction_end_at = datetime.utcnow() + timedelta(minutes=120)

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

    # Start auction (full implementation in пункт 11)
    from app.services.auction_service import AuctionService
    from app.bot.instance import bot
    auction = AuctionService(session=session, bot=bot)
    await auction.start_auction(order)

    await message.answer(
        "🎉 Ваша заявка создана! Ожидайте, пока операторы возьмут её в работу\n\n"
        "📋 Статус заявки вы можете посмотреть в разделе «Текущие заявки»",
        reply_markup=client_main_kb(),
    )
