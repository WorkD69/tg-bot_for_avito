from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import ClientMessagingStates
from app.db.models.message import MessageDirection
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.message_repo import MessageRepo
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

router = Router()


@router.callback_query(OrderCB.filter(F.action == "client_msg"), IsClient())
async def start_client_message(
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
    if order.status == OrderStatus.cancelled:
        await callback.answer("⚠️ Заявка отменена — переписка недоступна", show_alert=True)
        return
    if order.operator_id is None:
        await callback.answer("⏳ Оператор ещё не назначен", show_alert=True)
        return
    # Guard: cannot message yourself (admin testing as client)
    if order.operator_id == user.id:
        await callback.answer("⚠️ Нельзя написать самому себе", show_alert=True)
        return

    await state.set_state(ClientMessagingStates.waiting_message)
    await state.update_data(order_id=order.id, operator_id=order.operator_id)
    await callback.message.answer("✏️ Напишите сообщение оператору:")
    await callback.answer()


@router.message(ClientMessagingStates.waiting_message, F.text, IsClient())
async def send_client_message(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    operator_db_id: int = data["operator_id"]
    await state.clear()

    order = await OrderRepo(session).get_by_id(order_id)
    if not order or order.client_id != user.id:
        await message.answer("❌ Заявка не найдена")
        return
    # Re-validate self-message guard (operator may have changed since FSM started)
    if order.operator_id == user.id:
        await message.answer("⚠️ Нельзя написать самому себе")
        return

    # Resolve operator telegram_id from DB id
    operator = await UserRepo(session).get_by_id(operator_db_id)
    if not operator:
        await message.answer("❌ Оператор не найден")
        return

    await MessageRepo(session).create(
        order_id=order_id,
        sender_id=user.id,
        text=message.text,
        direction=MessageDirection.client_to_operator,
    )

    from app.bot.instance import bot
    client_name = user.full_name
    try:
        await bot.send_message(
            operator.telegram_id,  # correct: telegram_id, not DB id
            f"💬 Сообщение от клиента {client_name} по заявке №{order_id}:\n\n{message.text}",
        )
    except Exception:
        pass

    await message.answer("✅ Сообщение отправлено оператору")
