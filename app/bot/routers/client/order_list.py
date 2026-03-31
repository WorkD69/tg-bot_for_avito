from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.config import settings
from app.bot.formatters import format_client_card, format_client_history_card
from app.bot.keyboards.client_reply import BTN_CURRENT, BTN_HISTORY
from app.bot.keyboards.order_inline import (
    cancel_confirm_kb,
    client_active_order_kb,
    client_cancelled_order_kb,
    client_completed_order_kb,
    client_orders_list_kb,
)
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import OrderEditStates
from app.db.models.order import OrderStatus
from app.db.models.order_file import OrderFile
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
    if order.status != OrderStatus.pending:
        await callback.answer("⚠️ Отменить можно только заявку на рассмотрении", show_alert=True)
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
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.status != OrderStatus.pending:
        await callback.answer("⚠️ Заявку уже нельзя отменить", show_alert=True)
        return
    await OrderRepo(session).update_status(order, OrderStatus.cancelled)

    # Cancel APScheduler job
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
    order = await OrderRepo(session).get_by_id(order_id)
    if not order or order.client_id != user.id:
        await state.clear()
        await message.answer("❌ Заявка не найдена")
        return
    from datetime import datetime, timezone as tz
    ts = datetime.now(tz.utc).isoformat()
    new_entry = f"{ts}|{message.text.strip()}"
    order.comment = (order.comment + "\n---\n" + new_entry) if order.comment else new_entry
    await session.flush()
    await state.clear()
    await message.answer(f"✅ Комментарий к заявке №{order_id} добавлен")

    # Notify operator group with button
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


@router.callback_query(OrderCB.filter(F.action == "add_files"), IsClient())
async def add_files_start(
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
    session.add(OrderFile(order_id=order_id, telegram_file_id=file_id, file_type=file_type))
    await session.flush()
    await message.answer("✅ Файл добавлен — ещё или /done")


@router.message(OrderEditStates.waiting_files, F.text == "/done", IsClient())
async def add_files_done(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()
    await message.answer(f"✅ Файлы добавлены к заявке №{order_id}")

    # Notify operator group with button
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
        if sf.telegram_file_id.startswith("AgAC"):  # heuristic for photo
            await callback.message.answer_photo(sf.telegram_file_id)
        else:
            await callback.message.answer_document(sf.telegram_file_id)
