from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import MessagingStates
from app.db.models.message import MessageDirection
from app.db.models.user import User
from app.repositories.message_repo import MessageRepo
from app.repositories.order_repo import OrderRepo

router = Router()


@router.callback_query(OrderCB.filter(F.action == "msg"), IsOperator())
async def start_operator_message(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return

    await state.set_state(MessagingStates.waiting_message)
    await state.update_data(order_id=order.id, client_id=order.client_id)
    await callback.message.answer("✏️ Напишите сообщение клиенту:")
    await callback.answer()


@router.message(MessagingStates.waiting_message, F.text, IsOperator())
async def send_operator_message(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    client_id: int = data["client_id"]
    await state.clear()

    from app.repositories.user_repo import UserRepo
    client = await UserRepo(session).get_by_id(client_id)
    if not client:
        await message.answer("❌ Клиент не найден")
        return

    await MessageRepo(session).create(
        order_id=order_id,
        sender_id=user.id,
        text=message.text,
        direction=MessageDirection.op_to_client,
    )

    from app.bot.instance import bot
    op_name = f"@{user.username}" if user.username else user.full_name
    try:
        await bot.send_message(
            client.telegram_id,
            f"💬 Сообщение от оператора по заявке №{order_id}:\n\n{message.text}",
        )
    except Exception:
        pass

    await message.answer("✅ Сообщение отправлено клиенту")
