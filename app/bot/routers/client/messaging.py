from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import MessagingStates
from app.config import settings
from app.db.models.message import MessageDirection
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.message_repo import MessageRepo
from app.repositories.order_repo import OrderRepo

router = Router()


@router.callback_query(OrderCB.filter(F.action == "msg"), IsClient())
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
    if order.operator_id is None:
        await callback.answer("⏳ Оператор ещё не назначен", show_alert=True)
        return

    await state.set_state(MessagingStates.waiting_message)
    await state.update_data(order_id=order.id, operator_id=order.operator_id)
    await callback.message.answer("✏️ Напишите сообщение оператору:")
    await callback.answer()


@router.message(MessagingStates.waiting_message, F.text, IsClient())
async def send_client_message(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    operator_id: int = data["operator_id"]
    await state.clear()

    order = await OrderRepo(session).get_by_id(order_id, load_relations=True)
    if not order:
        await message.answer("❌ Заявка не найдена")
        return

    await MessageRepo(session).create(
        order_id=order_id,
        sender_id=user.id,
        text=message.text,
        direction=MessageDirection.client_to_op,
    )

    from app.bot.instance import bot
    client_name = f"@{user.username}" if user.username else user.full_name
    try:
        await bot.send_message(
            operator_id,
            f"💬 Сообщение от клиента {client_name} по заявке №{order_id}:\n\n{message.text}",
        )
    except Exception:
        pass

    await message.answer("✅ Сообщение отправлено оператору")
