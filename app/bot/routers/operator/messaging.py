from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import NegotCB, OrderCB
from app.bot.states.note import CounterOfferStates, MessagingStates, RequisitesStates
from app.db.models.message import MessageDirection
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.message_repo import MessageRepo
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

router = Router()


# ── Operator → client message ─────────────────────────────────────────────────

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
    # Guard: cannot message yourself
    if order.client_id == user.id:
        await callback.answer("⚠️ Нельзя написать самому себе", show_alert=True)
        return

    await state.set_state(MessagingStates.waiting_message)
    await state.update_data(order_id=order.id, client_id=order.client_id)
    await callback.message.answer("✏️ Напишите сообщение клиенту:")
    await callback.answer()


@router.message(MessagingStates.waiting_message, F.text, IsOperator())
async def send_operator_message(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    client_db_id: int = data["client_id"]
    await state.clear()

    # Re-validate
    order = await OrderRepo(session).get_by_id(order_id)
    if not order or order.operator_id != user.id:
        await message.answer("❌ Заявка не найдена или не ваша")
        return

    # Resolve client telegram_id
    client = await UserRepo(session).get_by_id(client_db_id)
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
    try:
        await bot.send_message(
            client.telegram_id,  # correct: telegram_id, not DB id
            f"💬 Сообщение от оператора по заявке №{order_id}:\n\n{message.text}",
        )
    except Exception:
        pass

    await message.answer("✅ Сообщение отправлено клиенту")


# ── Operator sends payment requisites ─────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "send_req"), IsOperator())
async def start_send_requisites(
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
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    await state.set_state(RequisitesStates.waiting_text)
    await state.update_data(order_id=order.id)
    await callback.message.answer(
        "💳 Отправьте реквизиты для оплаты (текстом):\n\n"
        "Например: «Карта Сбер: 1234 5678 9012 3456, получатель Иван И.»"
    )
    await callback.answer()


@router.message(RequisitesStates.waiting_text, F.text, IsOperator())
async def send_requisites_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    order = await OrderRepo(session).get_by_id(order_id)
    if not order or order.operator_id != user.id:
        await message.answer("❌ Заявка не найдена или не ваша")
        return
    if order.status != OrderStatus.awaiting_payment:
        await message.answer("⚠️ Заявка уже не ожидает оплаты")
        return

    client = await UserRepo(session).get_by_id(order.client_id)
    if not client:
        await message.answer("❌ Клиент не найден")
        return

    from app.bot.instance import bot
    from app.bot.keyboards.order_inline import client_awaiting_payment_kb
    from app.bot.formatters import _money
    try:
        await bot.send_message(
            client.telegram_id,
            f"💳 Реквизиты для оплаты заявки №{order_id}\n"
            f"Сумма: {_money(order.payment_amount)}\n\n"
            f"{message.text}\n\n"
            "После оплаты нажмите «Я оплатил»",
            reply_markup=client_awaiting_payment_kb(order_id),
        )
    except Exception:
        pass

    await message.answer("✅ Реквизиты отправлены клиенту")


# ── Operator responds to client counter-offer ─────────────────────────────────

@router.callback_query(NegotCB.filter(F.action == "accept"), IsOperator())
async def negot_accept(
    callback: CallbackQuery,
    callback_data: NegotCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    # Price was already updated when client sent counter — just send requisites now
    await callback.message.edit_reply_markup(reply_markup=None)
    from app.bot.keyboards.order_inline import send_requisites_kb
    await callback.message.answer(
        f"✅ Предложение клиента принято\n"
        f"Отправьте реквизиты для оплаты",
        reply_markup=send_requisites_kb(order.id),
    )
    await callback.answer()


@router.callback_query(NegotCB.filter(F.action == "counter"), IsOperator())
async def negot_counter(
    callback: CallbackQuery,
    callback_data: NegotCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    await state.set_state(CounterOfferStates.waiting_amount)
    await state.update_data(order_id=order.id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("💰 Введите вашу встречную сумму (число в рублях):")
    await callback.answer()


@router.message(CounterOfferStates.waiting_amount, F.text, IsOperator())
async def counter_offer_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(message.text.strip().replace(",", "."))
        if amount <= 0:
            raise ValueError
    except (ValueError, InvalidOperation):
        await message.answer("❌ Введите положительное число:")
        return

    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id(order_id)
    if not order or order.operator_id != user.id:
        await message.answer("❌ Заявка не найдена или не ваша")
        return
    if order.status != OrderStatus.awaiting_payment:
        await message.answer("⚠️ Заявка уже не ожидает оплаты")
        return

    await order_repo.update_payment_amount(order, amount)
    from app.db.models.order_log import OrderLogAction
    await order_repo.add_log(
        order_id=order_id, actor_id=user.id,
        action=OrderLogAction.price_updated,
        detail=f"operator counter: {amount} ₽",
    )

    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        from app.bot.instance import bot
        from app.bot.keyboards.order_inline import client_awaiting_payment_kb
        from app.bot.formatters import _money
        try:
            await bot.send_message(
                client.telegram_id,
                f"🔄 Оператор предлагает новую сумму по заявке №{order_id}: {_money(amount)}\n\n"
                "Выберите действие:",
                reply_markup=client_awaiting_payment_kb(order_id),
            )
        except Exception:
            pass

    await message.answer(f"✅ Встречное предложение {amount} ₽ отправлено клиенту")


@router.callback_query(NegotCB.filter(F.action == "cancel"), IsOperator())
async def negot_cancel_order(
    callback: CallbackQuery,
    callback_data: NegotCB,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    order_repo = OrderRepo(session)
    await order_repo.update_status(order, OrderStatus.cancelled)
    order.cancelled_by = "operator"
    await session.flush()
    from app.db.models.order_log import OrderLogAction
    await order_repo.add_log(
        order_id=order.id, actor_id=user.id,
        action=OrderLogAction.cancelled,
        detail="Operator cancelled during price negotiation",
    )

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✅ Заявка №{order.id} отменена")
    await callback.answer()

    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        from app.bot.instance import bot
        try:
            await bot.send_message(
                client.telegram_id,
                f"❌ К сожалению, заявка №{order.id} отменена оператором",
            )
        except Exception:
            pass
