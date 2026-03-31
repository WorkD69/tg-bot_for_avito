from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator, IsOperatorGroup
from app.bot.formatters import format_order_card
from app.bot.keyboards.callbacks import OrderCB
from app.bot.keyboards.operator_reply import BTN_DONE, BTN_FREE, BTN_MY
from app.bot.keyboards.order_inline import (
    free_order_card_kb,
    my_order_card_kb,
    orders_list_kb,
)
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()


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

    text = format_order_card(order)
    final = (OrderStatus.completed, OrderStatus.cancelled)
    if order.status in final:
        kb = None
    elif order.operator_id is None:
        kb = free_order_card_kb(order.id)
    else:
        kb = my_order_card_kb(order.id)

    try:
        await bot.send_message(user.telegram_id, text, reply_markup=kb)
        await callback.answer("✅ Карточка отправлена в личные сообщения")
    except Exception:
        await callback.answer(
            "⚠️ Не могу написать вам в личку — напишите боту /start в личных сообщениях, затем повторите",
            show_alert=True,
        )


# ── "view" callback IN OPERATOR DM ──────────────────────────────────────────

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

    text = format_order_card(order)
    final = (OrderStatus.completed, OrderStatus.cancelled)
    if order.status in final:
        kb = None
    elif order.operator_id is None:
        kb = free_order_card_kb(order.id)
    else:
        kb = my_order_card_kb(order.id)

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()
