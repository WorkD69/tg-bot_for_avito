from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.bid import BidStates
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()


@router.callback_query(OrderCB.filter(F.action == "bid"), IsOperator())
async def start_bid(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return
    if order.operator_id is not None:
        await callback.answer("⚠️ Заявка уже взята другим оператором", show_alert=True)
        return

    from app.db.models.order import OrderStatus
    if order.status != OrderStatus.pending:
        await callback.answer("⏰ Аукцион по этой заявке уже завершён", show_alert=True)
        return

    await state.set_state(BidStates.waiting_price)
    await state.update_data(order_id=order.id)
    await callback.message.answer(
        f"📋 Заявка №{order.id} — бюджет клиента: {order.budget} ₽\n"
        "💰 Введите вашу ставку (число в рублях):"
    )
    await callback.answer()


@router.message(BidStates.waiting_price, F.text, IsOperator())
async def got_bid_price(message: Message, state: FSMContext, session: AsyncSession, user: User):
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except (ValueError, Exception):
        await message.answer("❌ Введите положительное число, например 1200:")
        return

    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    from app.services.auction_service import AuctionService
    from app.bot.instance import bot

    auction = AuctionService(session=session, bot=bot)
    await auction.place_bid(order_id=order_id, operator_id=user.id, amount=amount)
