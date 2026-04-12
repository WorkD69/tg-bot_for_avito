from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.config import settings
from app.bot.formatters import format_client_card, format_client_history_card, _money
from app.bot.keyboards.client_reply import BTN_CURRENT, BTN_HISTORY
from app.bot.keyboards.order_inline import (
    cancel_confirm_kb,
    client_active_order_kb,
    client_awaiting_payment_kb,
    client_cancelled_order_kb,
    client_completed_order_kb,
    client_orders_list_kb,
)
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import NegotiationStates, OrderEditStates
from app.db.models.order import OrderStatus
from app.db.models.order_file import OrderFile
from app.db.models.order_log import OrderLogAction
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()

MAX_FILES = 10


@router.message(F.text == BTN_CURRENT, IsClient())
async def current_orders(message: Message, session: AsyncSession, user: User):
    orders = await OrderRepo(session).get_client_active_orders(user.id)
    if not orders:
        await message.answer("📭 У вас нет активных заявок")
        return
    await message.answer("📋 Ваши текущие заявки:", reply_markup=client_orders_list_kb(orders))


@router.message(F.text == BTN_HISTORY, IsClient())
async def history_orders(message: Message, session: AsyncSession, user: User):
    orders = await OrderRepo(session).get_client_history(user.id)
    if not orders:
        await message.answer("📭 История заявок пуста")
        return
    await message.answer("🗂 История заявок:", reply_markup=client_orders_list_kb(orders))


@router.callback_query(OrderCB.filter(F.action == "client_view"), IsClient())
async def view_order_client(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    if order.status == OrderStatus.completed:
        text = format_client_history_card(order)
        kb = client_completed_order_kb(order.id)
    elif order.status == OrderStatus.cancelled:
        text = format_client_history_card(order)
        kb = client_cancelled_order_kb(order.id)
    elif order.status == OrderStatus.awaiting_payment:
        text = format_client_card(order)
        # In online (Robokassa) mode the link is in the message; "Я оплатил" is for manual mode only
        kb = client_awaiting_payment_kb(order.id, show_paid_btn=not bool(settings.robokassa_login))
    else:
        text = format_client_card(order)
        can_cancel = order.status == OrderStatus.pending
        kb = client_active_order_kb(order.id, can_cancel=can_cancel)

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "client_refresh"), IsClient())
async def refresh_order_client(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    if order.status == OrderStatus.completed:
        text = format_client_history_card(order)
        kb = client_completed_order_kb(order.id)
    elif order.status == OrderStatus.cancelled:
        text = format_client_history_card(order)
        kb = client_cancelled_order_kb(order.id)
    elif order.status == OrderStatus.awaiting_payment:
        text = format_client_card(order)
        kb = client_awaiting_payment_kb(order.id, show_paid_btn=not bool(settings.robokassa_login))
    else:
        text = format_client_card(order)
        can_cancel = order.status == OrderStatus.pending
        kb = client_active_order_kb(order.id, can_cancel=can_cancel)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer("✅ Обновлено")
    except Exception:
        await callback.answer("✅ Уже актуально")


@router.callback_query(OrderCB.filter(F.action == "back_list"), IsClient())
async def back_to_orders_list(
    callback: CallbackQuery,
    session: AsyncSession,
    callback_data: OrderCB,
    user: User,
):
    try:
        await callback.message.delete()
    except Exception:
        pass
    orders = await OrderRepo(session).get_client_active_orders(user.id)
    if not orders:
        await callback.message.answer("📭 У вас нет активных заявок")
    else:
        await callback.message.answer("📋 Ваши текущие заявки:", reply_markup=client_orders_list_kb(orders))
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "back_history"), IsClient())
async def back_to_history_list(
    callback: CallbackQuery,
    session: AsyncSession,
    callback_data: OrderCB,
    user: User,
):
    try:
        await callback.message.delete()
    except Exception:
        pass
    orders = await OrderRepo(session).get_client_history(user.id)
    if not orders:
        await callback.message.answer("📭 История заявок пуста")
    else:
        await callback.message.answer("🗂 История заявок:", reply_markup=client_orders_list_kb(orders))
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel"), IsClient())
async def cancel_order_prompt(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    # Allow cancel for pending OR awaiting_payment — but NOT after payment is already received
    if order.status not in (OrderStatus.pending, OrderStatus.awaiting_payment):
        await callback.answer("⚠️ Отменить можно только заявку в ожидании", show_alert=True)
        return
    if order.payment_received_at:
        await callback.answer(
            "⚠️ Отмена недоступна — оплата уже зафиксирована\nОбратитесь к администратору",
            show_alert=True,
        )
        return
    await callback.message.answer(
        f"❓ Вы уверены, что хотите отменить заявку №{order.id}?",
        reply_markup=cancel_confirm_kb(order.id),
    )
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel_no"), IsClient())
async def cancel_order_no(callback: CallbackQuery, callback_data: OrderCB):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel_yes"), IsClient())
async def cancel_order_confirm(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
    post_commit: list,
):
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status not in (OrderStatus.pending, OrderStatus.awaiting_payment):
        await callback.answer("⚠️ Заявку уже нельзя отменить", show_alert=True)
        return
    if order.payment_received_at:
        await callback.answer(
            "⚠️ Отмена недоступна — оплата уже зафиксирована",
            show_alert=True,
        )
        return

    # Capture pre-cancel status before atomic transition (for scheduler removal)
    was_pending = order.status == OrderStatus.pending

    await order_repo.cancel(order, "client")
    await order_repo.add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.cancelled,
        detail="Client cancelled",
    )

    # Cancel scheduler job if the order was still in auction
    if was_pending:
        from app.services.auction_service import _remove_scheduler_job
        _remove_scheduler_job(order.id)

    await callback.message.answer(f"✅ Заявка №{order.id} отменена")
    await callback.answer()

    # Notify operator group AFTER commit — deferred via post_commit
    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    post_commit.append(_bot.send_message(
        settings.operator_group_id,
        f"❌ Заявка №{order.id} отменена клиентом",
        reply_markup=group_new_order_kb(order.id),
    ))

    # If operator was already assigned (awaiting_payment) — notify them directly in DM
    # Group notifications are easy to miss when operator is handling multiple orders
    if order.operator_id is not None:
        from app.repositories.user_repo import UserRepo
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            post_commit.append(_bot.send_message(
                operator.telegram_id,
                f"❌ Клиент отменил заявку №{order.id} — оплата не будет произведена",
            ))


# ── awaiting_payment: client confirmed payment ────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "pay"), IsClient())
async def client_paid(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
    post_commit: list,
):
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return
    # Idempotency: if payment_received_at is already set, this is a duplicate press
    if order.payment_received_at:
        await callback.answer(
            "ℹ️ Оплата уже зафиксирована — ждите подтверждения администратора",
            show_alert=True,
        )
        return

    from datetime import datetime, timezone
    order.payment_received_at = datetime.now(timezone.utc)
    await session.flush()
    await order_repo.add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.payment_received,
        detail="Client pressed 'Я оплатил'",
    )

    # Admin notification deferred — fires after commit
    from app.bot.instance import bot as _bot
    post_commit.append(_bot.send_message(
        settings.admin_telegram_id,
        f"💳 Клиент сообщил об оплате заявки №{order.id}\n"
        f"Сумма: {_money(order.payment_amount)}\n"
        f"Подтвердить: /confirmpayment {order.id}",
    ))

    await callback.message.answer(
        "✅ Спасибо! Мы уведомили администратора об оплате\n"
        "Ожидайте подтверждения — обычно это занимает несколько минут"
    )
    await callback.answer()


# ── awaiting_payment: client wants to negotiate ───────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "negotiate"), IsClient())
async def client_negotiate(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    # FSM guard — don't silently overwrite an active FSM (e.g. order creation, file upload)
    current_state = await state.get_state()
    if current_state is not None and current_state != str(NegotiationStates.waiting_text):
        await callback.answer(
            "⚠️ Вы уже выполняете другое действие — завершите его или отмените через /cancel",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    await state.set_state(NegotiationStates.waiting_text)
    await state.update_data(order_id=order.id)
    await callback.message.answer(
        "💬 Напишите ваш вопрос или предложите встречную сумму\n\n"
        "Например: «Готов оплатить 1200 ₽» или «Уточните, что входит в работу»\n\n"
        "Для отмены — /cancel"
    )
    await callback.answer()


@router.message(NegotiationStates.waiting_text, F.text, IsClient())
async def client_negotiate_done(message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list):
    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id(order_id)
    if not order or order.client_id != user.id:
        await message.answer("❌ Заявка не найдена")
        return
    if order.status != OrderStatus.awaiting_payment:
        await message.answer("⚠️ Заявка уже не ожидает оплаты")
        return

    # Try to parse as counter price — do NOT change payment_amount yet.
    # The proposed amount is encoded in the operator's Accept button callback_data.
    # Official payment_amount only changes when operator explicitly accepts.
    from decimal import Decimal, InvalidOperation
    counter_amount: Decimal | None = None
    try:
        counter_amount = Decimal(message.text.strip().replace(",", ".").replace(" ", "").replace("₽", ""))
        if counter_amount <= 0:
            counter_amount = None
    except InvalidOperation:
        pass

    # Notify operator — proposed amount travels via NegotCB, not via DB
    from app.repositories.user_repo import UserRepo
    operator = await UserRepo(session).get_by_id(order.operator_id)
    if operator:
        from app.bot.instance import bot
        from app.bot.keyboards.order_inline import negot_operator_kb
        client_name = user.full_name
        proposed_str = str(counter_amount) if counter_amount else ""
        counter_label = f"\n💰 Предложенная сумма: {_money(counter_amount)}" if counter_amount else ""
        post_commit.append(bot.send_message(
            operator.telegram_id,
            f"💬 Сообщение от клиента {client_name} по заявке №{order_id}:{counter_label}\n\n"
            f"{message.text}",
            reply_markup=negot_operator_kb(order_id, proposed_amount=proposed_str, revision=order.payment_revision),
        ))

    await message.answer("✅ Ваше сообщение отправлено оператору — ожидайте ответа")


# ── Comment ───────────────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "add_comment"), IsClient())
async def add_comment_start(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    current_state = await state.get_state()
    if current_state in (str(OrderEditStates.waiting_comment), str(OrderEditStates.waiting_files)):
        await callback.answer(
            "⚠️ Вы уже редактируете заявку — завершите текущее действие или отправьте /done",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status in (OrderStatus.completed, OrderStatus.cancelled):
        await callback.answer("⚠️ Нельзя редактировать завершённую или отменённую заявку", show_alert=True)
        return
    await state.set_state(OrderEditStates.waiting_comment)
    await state.update_data(order_id=order.id)
    await callback.message.answer("✏️ Введите новый комментарий к заявке:")
    await callback.answer()


MAX_COMMENT_LEN = 2000


@router.message(OrderEditStates.waiting_comment, F.text, IsClient())
async def add_comment_done(
    message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list
):
    raw = message.text.strip()
    if not raw:
        await message.answer(
            "⚠️ Комментарий не может быть пустым\n"
            "Введите текст или нажмите /cancel для отмены"
        )
        return
    if len(raw) > MAX_COMMENT_LEN:
        await message.answer(
            f"❌ Слишком длинный комментарий ({len(raw)} символов)\n"
            f"Максимум {MAX_COMMENT_LEN} символов — сократите текст"
        )
        return
    data = await state.get_data()
    order_id: int = data["order_id"]
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id(order_id)
    if not order or order.client_id != user.id:
        await state.clear()
        await message.answer("❌ Заявка не найдена")
        return
    if order.status in (OrderStatus.completed, OrderStatus.cancelled):
        await state.clear()
        await message.answer("⚠️ Заявка уже завершена или отменена — комментарий не добавлен")
        return
    from datetime import datetime, timezone as tz
    ts = datetime.now(tz.utc).isoformat()
    new_entry = f"{ts}|{raw}"
    order.comment = (order.comment + "\n---\n" + new_entry) if order.comment else new_entry
    await session.flush()
    await order_repo.add_log(
        order_id=order_id, actor_id=user.id, action=OrderLogAction.comment_added,
    )
    await state.clear()
    await message.answer(f"✅ Комментарий к заявке №{order_id} добавлен")

    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    post_commit.append(_bot.send_message(
        settings.operator_group_id,
        f"✏️ К заявке №{order_id} добавлен комментарий клиентом",
        reply_markup=group_new_order_kb(order_id),
    ))

    # Notify assigned operator directly in DM — group notifications are easy to miss
    if order.operator_id is not None:
        from app.repositories.user_repo import UserRepo
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            post_commit.append(_bot.send_message(
                operator.telegram_id,
                f"✏️ Клиент добавил комментарий к заявке №{order_id} — проверьте обновление",
            ))


# ── Files ─────────────────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "add_files"), IsClient())
async def add_files_start(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    current_state = await state.get_state()
    if current_state in (str(OrderEditStates.waiting_comment), str(OrderEditStates.waiting_files)):
        await callback.answer(
            "⚠️ Вы уже редактируете заявку — завершите текущее действие или отправьте /done",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status in (OrderStatus.completed, OrderStatus.cancelled):
        await callback.answer("⚠️ Нельзя добавить файлы к завершённой заявке", show_alert=True)
        return
    if len(order.files) >= MAX_FILES:
        await callback.answer(f"⚠️ Достигнут лимит {MAX_FILES} файлов", show_alert=True)
        return
    await state.set_state(OrderEditStates.waiting_files)
    await state.update_data(order_id=order.id, files_added=0)
    await callback.message.answer("📎 Отправьте файлы, когда закончите — /done:")
    await callback.answer()


@router.message(OrderEditStates.waiting_files, F.photo, IsClient())
async def add_files_photo(message: Message, state: FSMContext, session: AsyncSession, user: User):
    await _save_file(message, state, session, file_id=message.photo[-1].file_id, file_type="photo")


@router.message(OrderEditStates.waiting_files, F.document, IsClient())
async def add_files_document(message: Message, state: FSMContext, session: AsyncSession, user: User):
    await _save_file(message, state, session, file_id=message.document.file_id, file_type="document")


async def _save_file(message: Message, state: FSMContext, session: AsyncSession, file_id: str, file_type: str):
    data = await state.get_data()
    order_id: int = data["order_id"]
    # Re-check file limit on each save
    from sqlalchemy import select, func
    count_result = await session.execute(
        select(func.count()).where(OrderFile.order_id == order_id)
    )
    current_count = count_result.scalar() or 0
    if current_count >= MAX_FILES:
        await message.answer(f"⚠️ Достигнут лимит {MAX_FILES} файлов — отправьте /done")
        return
    session.add(OrderFile(order_id=order_id, telegram_file_id=file_id, file_type=file_type))
    await session.flush()
    data = await state.get_data()
    files_added = data.get("files_added", 0) + 1
    await state.update_data(files_added=files_added)
    await message.answer(f"✅ Файл добавлен ({current_count + 1}/{MAX_FILES}) — ещё или /done")


@router.message(OrderEditStates.waiting_files, F.text == "/done", IsClient())
async def add_files_done(
    message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list
):
    data = await state.get_data()
    order_id: int = data["order_id"]
    if not data.get("files_added", 0):
        await state.clear()
        await message.answer("❌ Вы не отправили ни одного файла — добавление отменено")
        return
    await state.clear()

    order_repo = OrderRepo(session)
    # Re-validate: order may have changed status since FSM started
    order = await order_repo.get_by_id(order_id)
    if not order or order.client_id != user.id:
        await message.answer("❌ Заявка не найдена")
        return
    if order.status in (OrderStatus.completed, OrderStatus.cancelled):
        await message.answer("⚠️ Заявка уже завершена или отменена — файлы не приняты")
        return

    await order_repo.add_log(
        order_id=order_id, actor_id=user.id, action=OrderLogAction.files_added,
    )
    await message.answer(f"✅ Файлы добавлены к заявке №{order_id}")

    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    post_commit.append(_bot.send_message(
        settings.operator_group_id,
        f"📎 К заявке №{order_id} добавлены файлы клиентом",
        reply_markup=group_new_order_kb(order_id),
    ))

    # Notify assigned operator directly in DM — group notifications are easy to miss
    if order.operator_id is not None:
        from app.repositories.user_repo import UserRepo
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            post_commit.append(_bot.send_message(
                operator.telegram_id,
                f"📎 Клиент добавил файлы к заявке №{order_id} — проверьте обновление",
            ))


# ── Dispute (client) ─────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "dispute"), IsClient())
async def client_dispute(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
    post_commit: list,
):
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status != OrderStatus.completed:
        await callback.answer("⚠️ Оспорить можно только выполненную заявку", show_alert=True)
        return

    await order_repo.add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.dispute_opened,
        detail="Client opened dispute",
    )

    from app.bot.instance import bot as _bot
    client_name = f"@{user.username}" if user.username else user.full_name
    post_commit.append(_bot.send_message(
        settings.admin_telegram_id,
        f"⚠️ Клиент {client_name} оспаривает результат по заявке №{order.id}\n"
        f"Свяжитесь с клиентом и рассмотрите заявку",
    ))

    await callback.message.answer(
        "⚠️ Спор открыт — администратор получил уведомление и свяжется с вами"
    )
    await callback.answer()


# ── View solution (client) ────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "client_solution"), IsClient())
async def view_solution(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    if not order.solution_files:
        await callback.answer("⏳ Решение ещё не загружено", show_alert=True)
        return

    await callback.answer()

    await callback.message.answer(f"📎 Решение по заявке №{order.id}:")
    for sf in order.solution_files:
        if sf.file_type == "photo":
            await callback.message.answer_photo(sf.telegram_file_id)
        else:
            await callback.message.answer_document(sf.telegram_file_id)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    await callback.message.answer(
        "⬆️ Файлы выше",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="← История заявок",
                callback_data=OrderCB(order_id=order.id, action="back_history").pack(),
            )
        ]]),
    )


# ── Fallback handlers for non-text input in client FSM states ─────────────────

@router.message(NegotiationStates.waiting_text, IsClient())
async def negotiate_unexpected_type(message: Message):
    """Client sent a file/sticker/voice instead of a negotiation message."""
    await message.answer(
        "💬 Напишите сообщение текстом\n"
        "Например: «Готов оплатить 1200 ₽» или уточняющий вопрос\n"
        "Для отмены — /cancel"
    )


@router.message(OrderEditStates.waiting_comment, IsClient())
async def edit_comment_unexpected_type(message: Message):
    """Client sent a file instead of a comment text."""
    await message.answer(
        "✏️ Введите комментарий текстом\n"
        "Для отмены — /cancel"
    )


@router.message(OrderEditStates.waiting_files, IsClient())
async def edit_files_unexpected_type(message: Message):
    """Client sent an unsupported file type (video, voice, sticker, etc.)."""
    await message.answer(
        "⚠️ Поддерживаются только фото и документы\n"
        "Отправьте файл нужного типа или /done чтобы завершить"
    )
