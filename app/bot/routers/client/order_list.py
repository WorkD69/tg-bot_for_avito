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
        kb = client_awaiting_payment_kb(order.id)
    else:
        text = format_client_card(order)
        can_cancel = order.status == OrderStatus.pending
        kb = client_active_order_kb(order.id, can_cancel=can_cancel)

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "back_list"), IsClient())
async def back_to_orders_list(
    callback: CallbackQuery,
    session: AsyncSession,
    callback_data: OrderCB,
    user: User,
):
    await callback.message.delete()
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
    await callback.message.delete()
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
    # Allow cancel for pending OR awaiting_payment
    if order.status not in (OrderStatus.pending, OrderStatus.awaiting_payment):
        await callback.answer("⚠️ Отменить можно только заявку в ожидании", show_alert=True)
        return
    await callback.message.answer(
        f"❓ Вы уверены, что хотите отменить заявку №{order.id}?",
        reply_markup=cancel_confirm_kb(order.id),
    )
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel_no"), IsClient())
async def cancel_order_no(callback: CallbackQuery, callback_data: OrderCB):
    await callback.message.delete()
    await callback.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel_yes"), IsClient())
async def cancel_order_confirm(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status not in (OrderStatus.pending, OrderStatus.awaiting_payment):
        await callback.answer("⚠️ Заявку уже нельзя отменить", show_alert=True)
        return

    await order_repo.update_status(order, OrderStatus.cancelled)
    order.cancelled_by = "client"
    await session.flush()
    await order_repo.add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.cancelled,
        detail="Client cancelled",
    )

    # Cancel scheduler job if pending
    if order.status == OrderStatus.pending:
        from app.services.auction_service import _remove_scheduler_job
        _remove_scheduler_job(order.id)

    await callback.message.answer(f"✅ Заявка №{order.id} отменена")
    await callback.answer()

    # Notify operator group with button
    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    try:
        await _bot.send_message(
            settings.operator_group_id,
            f"❌ Заявка №{order.id} отменена клиентом",
            reply_markup=group_new_order_kb(order.id),
        )
    except Exception:
        pass


# ── awaiting_payment: client confirmed payment ────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "pay"), IsClient())
async def client_paid(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return
    if order.payment_received_at:
        await callback.answer("ℹ️ Оплата уже зафиксирована — ждите подтверждения администратора", show_alert=True)
        return

    from datetime import datetime, timezone
    order.payment_received_at = datetime.now(timezone.utc)
    await session.flush()
    await OrderRepo(session).add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.payment_received,
        detail="Client pressed 'Я оплатил'",
    )

    # Notify admin
    from app.bot.instance import bot as _bot
    try:
        await _bot.send_message(
            settings.admin_telegram_id,
            f"💳 Клиент сообщил об оплате заявки №{order.id}\n"
            f"Сумма: {_money(order.payment_amount)}\n"
            f"Подтвердить: /confirmpayment {order.id}",
        )
    except Exception:
        pass

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
        "Например: «Готов оплатить 1200 ₽» или «Уточните, что входит в работу»"
    )
    await callback.answer()


@router.message(NegotiationStates.waiting_text, F.text, IsClient())
async def client_negotiate_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
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

    # Try to parse as counter price
    from decimal import Decimal, InvalidOperation
    counter_amount = None
    try:
        counter_amount = Decimal(message.text.strip().replace(",", ".").replace(" ", "").replace("₽", ""))
        if counter_amount <= 0:
            counter_amount = None
    except InvalidOperation:
        pass

    if counter_amount:
        await order_repo.update_payment_amount(order, counter_amount)
        await order_repo.add_log(
            order_id=order_id, actor_id=user.id,
            action=OrderLogAction.price_updated,
            detail=f"client counter: {counter_amount} ₽",
        )

    # Notify operator
    from app.repositories.user_repo import UserRepo
    operator = await UserRepo(session).get_by_id(order.operator_id)
    if operator:
        from app.bot.instance import bot
        from app.bot.keyboards.order_inline import negot_operator_kb
        client_name = f"@{user.username}" if user.username else user.full_name
        counter_str = f"\n💰 Предложенная сумма: {_money(counter_amount)}" if counter_amount else ""
        try:
            await bot.send_message(
                operator.telegram_id,
                f"💬 Сообщение от клиента {client_name} по заявке №{order_id}:{counter_str}\n\n"
                f"{message.text}",
                reply_markup=negot_operator_kb(order_id),
            )
        except Exception:
            pass

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
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    await state.set_state(OrderEditStates.waiting_comment)
    await state.update_data(order_id=order.id)
    await callback.message.answer("✏️ Введите новый комментарий к заявке:")
    await callback.answer()


@router.message(OrderEditStates.waiting_comment, F.text, IsClient())
async def add_comment_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id(order_id)
    if not order or order.client_id != user.id:
        await state.clear()
        await message.answer("❌ Заявка не найдена")
        return
    from datetime import datetime, timezone as tz
    ts = datetime.now(tz.utc).isoformat()
    new_entry = f"{ts}|{message.text.strip()}"
    order.comment = (order.comment + "\n---\n" + new_entry) if order.comment else new_entry
    await session.flush()
    await order_repo.add_log(
        order_id=order_id, actor_id=user.id, action=OrderLogAction.comment_added,
    )
    await state.clear()
    await message.answer(f"✅ Комментарий к заявке №{order_id} добавлен")

    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    try:
        await _bot.send_message(
            settings.operator_group_id,
            f"✏️ К заявке №{order_id} добавлен комментарий клиентом",
            reply_markup=group_new_order_kb(order_id),
        )
    except Exception:
        pass


# ── Files ─────────────────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "add_files"), IsClient())
async def add_files_start(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if len(order.files) >= MAX_FILES:
        await callback.answer(f"⚠️ Достигнут лимит {MAX_FILES} файлов", show_alert=True)
        return
    await state.set_state(OrderEditStates.waiting_files)
    await state.update_data(order_id=order.id)
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
    await message.answer(f"✅ Файл добавлен ({current_count + 1}/{MAX_FILES}) — ещё или /done")


@router.message(OrderEditStates.waiting_files, F.text == "/done", IsClient())
async def add_files_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    order_repo = OrderRepo(session)
    await order_repo.add_log(
        order_id=order_id, actor_id=user.id, action=OrderLogAction.files_added,
    )
    await message.answer(f"✅ Файлы добавлены к заявке №{order_id}")

    from app.bot.instance import bot as _bot
    from app.bot.keyboards.order_inline import group_new_order_kb
    try:
        await _bot.send_message(
            settings.operator_group_id,
            f"📎 К заявке №{order_id} добавлены файлы клиентом",
            reply_markup=group_new_order_kb(order_id),
        )
    except Exception:
        pass


# ── View solution (client) ────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "solution"), IsClient())
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
