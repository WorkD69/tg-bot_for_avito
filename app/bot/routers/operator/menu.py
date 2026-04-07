from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator, IsOperatorGroup
from app.bot.formatters import format_order_card
from app.bot.keyboards.callbacks import OrderCB
from app.bot.keyboards.operator_reply import BTN_DONE, BTN_FREE, BTN_MY
from app.bot.keyboards.order_inline import (
    awaiting_payment_operator_kb,
    files_view_kb,
    free_order_card_kb,
    my_order_card_kb,
    operator_refresh_only_kb,
    orders_list_kb,
)
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()


def _pick_kb(order, viewer_id: int):
    """Pick the right keyboard based on order state and who's viewing."""
    final = (OrderStatus.completed, OrderStatus.cancelled)
    if order.status in final:
        return operator_refresh_only_kb(order.id)
    if order.operator_id is None:
        return free_order_card_kb(order.id)
    if order.operator_id == viewer_id:
        if order.status == OrderStatus.awaiting_payment:
            return awaiting_payment_operator_kb(order.id)
        return my_order_card_kb(order.id)
    # Someone else's assigned order — read-only, refresh only
    return operator_refresh_only_kb(order.id)


# ── Reply buttons IN GROUP → send list to operator DM ────────────────────────

@router.message(IsOperatorGroup(), IsOperator(), F.text == BTN_FREE)
async def group_free_orders(message: Message, session: AsyncSession, bot: Bot, user: User):
    orders = await OrderRepo(session).get_free_orders()
    text = "📋 Свободные заявки:" if orders else "✅ Свободных заявок нет"
    kb = orders_list_kb(orders) if orders else None
    await bot.send_message(user.telegram_id, text, reply_markup=kb)


@router.message(IsOperatorGroup(), IsOperator(), F.text == BTN_MY)
async def group_my_orders(message: Message, session: AsyncSession, bot: Bot, user: User):
    orders = await OrderRepo(session).get_operator_active_orders(user.id)
    text = "📋 Ваши заявки:" if orders else "📭 У вас нет активных заявок"
    kb = orders_list_kb(orders) if orders else None
    await bot.send_message(user.telegram_id, text, reply_markup=kb)


@router.message(IsOperatorGroup(), IsOperator(), F.text == BTN_DONE)
async def group_done_orders(message: Message, session: AsyncSession, bot: Bot, user: User):
    orders = await OrderRepo(session).get_operator_completed_orders(user.id)
    text = "🗂 История выполненных заявок:" if orders else "📭 Нет выполненных заявок"
    kb = orders_list_kb(orders) if orders else None
    await bot.send_message(user.telegram_id, text, reply_markup=kb)


# ── Same buttons IN OPERATOR DM → respond directly in DM ─────────────────────
# Group handlers have IsOperatorGroup() so they only fire in group chat.
# These DM handlers fire when operator uses the operator_dm_kb in private chat.

@router.message(IsOperator(), F.text == BTN_FREE)
async def dm_free_orders(message: Message, session: AsyncSession, user: User):
    orders = await OrderRepo(session).get_free_orders()
    text = "📋 Свободные заявки:" if orders else "✅ Свободных заявок нет"
    kb = orders_list_kb(orders) if orders else None
    await message.answer(text, reply_markup=kb)


@router.message(IsOperator(), F.text == BTN_MY)
async def dm_my_orders(message: Message, session: AsyncSession, user: User):
    orders = await OrderRepo(session).get_operator_active_orders(user.id)
    text = "📋 Ваши заявки:" if orders else "📭 У вас нет активных заявок"
    kb = orders_list_kb(orders) if orders else None
    await message.answer(text, reply_markup=kb)


@router.message(IsOperator(), F.text == BTN_DONE)
async def dm_done_orders(message: Message, session: AsyncSession, user: User):
    orders = await OrderRepo(session).get_operator_completed_orders(user.id)
    text = "🗂 История выполненных заявок:" if orders else "📭 Нет выполненных заявок"
    kb = orders_list_kb(orders) if orders else None
    await message.answer(text, reply_markup=kb)


# ── "Перейти к заявке" pressed IN GROUP → open card in operator DM ───────────

@router.callback_query(OrderCB.filter(F.action == "view"), IsOperatorGroup(), IsOperator())
async def group_view_order(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    bot: Bot,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    from app.db.models.user import UserRole
    text = format_order_card(order, is_admin=(user.role == UserRole.admin), viewer_id=user.id)
    kb = _pick_kb(order, user.id)

    try:
        await bot.send_message(user.telegram_id, text, reply_markup=kb)
        await callback.answer("✅ Карточка отправлена в личные сообщения")
    except Exception:
        await callback.answer(
            "⚠️ Не могу написать вам в личку — напишите боту /start в личных сообщениях, затем повторите",
            show_alert=True,
        )


# ── "view" callback IN OPERATOR DM ───────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "view"), IsOperator())
async def dm_view_order(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    from app.db.models.user import UserRole
    text = format_order_card(order, is_admin=(user.role == UserRole.admin), viewer_id=user.id)
    kb = _pick_kb(order, user.id)

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


# ── "← Назад" from files view → restore order card ──────────────────────────

@router.callback_query(OrderCB.filter(F.action == "back"), IsOperator())
async def back_to_card(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    from app.db.models.user import UserRole
    text = format_order_card(order, is_admin=(user.role == UserRole.admin), viewer_id=user.id)
    kb = _pick_kb(order, user.id)

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ── "🔄 Обновить" — refresh card in-place ────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "refresh"), IsOperator())
async def refresh_order_card(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    from app.db.models.user import UserRole
    text = format_order_card(order, is_admin=(user.role == UserRole.admin), viewer_id=user.id)
    kb = _pick_kb(order, user.id)

    try:
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer("✅ Обновлено")
    except Exception:
        await callback.answer("✅ Уже актуально")
