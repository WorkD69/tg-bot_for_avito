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
        direction=MessageDirection.operator_to_client,
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
        # Manual payment mode — client confirms via "Я оплатил" button
        await bot.send_message(
            client.telegram_id,
            f"💳 Реквизиты для оплаты заявки №{order_id}\n"
            f"Сумма: {_money(order.payment_amount)}\n\n"
            f"{message.text}\n\n"
            "После оплаты нажмите «Я оплатил»",
            reply_markup=client_awaiting_payment_kb(order_id, show_paid_btn=True),
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
    from decimal import Decimal, InvalidOperation
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.awaiting_payment:
        await callback.answer("⚠️ Заявка уже не ожидает оплаты", show_alert=True)
        return

    await callback.message.edit_reply_markup(reply_markup=None)

    # Apply the client's proposed amount — only NOW does payment_amount change officially.
    # proposed_amount is carried in callback_data (encoded when client sent the counter).
    proposed_amount: Decimal | None = None
    if callback_data.proposed_amount:
        try:
            proposed_amount = Decimal(callback_data.proposed_amount)
        except InvalidOperation:
            pass

    if proposed_amount and proposed_amount > 0:
        await order_repo.update_agreed_price(order, proposed_amount)
        from app.db.models.order_log import OrderLogAction
        await order_repo.add_log(
            order_id=order.id, actor_id=user.id,
            action=OrderLogAction.price_updated,
            detail=f"operator accepted client offer: {proposed_amount} ₽",
        )
        from app.bot.formatters import _money
        new_amount_str = _money(proposed_amount)
    else:
        # General message accepted (no price change) — just prompt to send requisites
        new_amount_str = None

    # Notify client about the accepted price and next step
    client = await UserRepo(session).get_by_id(order.client_id)
    from app.config import settings
    if client:
        from app.bot.instance import bot
        from app.bot.keyboards.order_inline import client_awaiting_payment_kb
        try:
            if settings.robokassa_login and proposed_amount:
                from app.services.payment_service import PaymentService
                link = PaymentService().generate_link(
                    invoice_id=order.payment_invoice_id,
                    amount=order.payment_amount,
                )
                await bot.send_message(
                    client.telegram_id,
                    f"✅ Оператор принял вашу цену: {new_amount_str}\n"
                    f"💳 Новая ссылка для оплаты: {link}",
                    reply_markup=client_awaiting_payment_kb(order.id, show_paid_btn=False),
                )
            else:
                price_line = f"✅ Оператор принял цену: {new_amount_str}\n" if new_amount_str else "✅ Оператор согласен\n"
                await bot.send_message(
                    client.telegram_id,
                    f"{price_line}⏳ Ожидайте — оператор отправит обновлённые реквизиты для оплаты",
                    reply_markup=client_awaiting_payment_kb(order.id, show_paid_btn=True),
                )
        except Exception:
            pass

    from app.bot.keyboards.order_inline import send_requisites_kb
    price_confirm = f" по новой цене {new_amount_str}" if new_amount_str else ""
    await callback.message.answer(
        f"✅ Предложение клиента принято{price_confirm}\n"
        "Отправьте клиенту реквизиты для оплаты",
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

    # Operator's counter IS authoritative — operator sets the price.
    # update_agreed_price resets payment state so the new price must be paid fresh.
    await order_repo.update_agreed_price(order, amount)
    from app.db.models.order_log import OrderLogAction
    await order_repo.add_log(
        order_id=order_id, actor_id=user.id,
        action=OrderLogAction.price_updated,
        detail=f"operator counter: {amount} ₽",
    )

    client = await UserRepo(session).get_by_id(order.client_id)
    from app.config import settings
    from app.bot.formatters import _money
    from app.bot.keyboards.order_inline import client_awaiting_payment_kb, send_requisites_kb
    if client:
        from app.bot.instance import bot
        try:
            if settings.robokassa_login:
                from app.services.payment_service import PaymentService
                link = PaymentService().generate_link(
                    invoice_id=order.payment_invoice_id,
                    amount=order.payment_amount,
                )
                await bot.send_message(
                    client.telegram_id,
                    f"🔄 Оператор обновил сумму по заявке №{order_id}: {_money(amount)}\n"
                    f"💳 Новая ссылка для оплаты: {link}",
                    reply_markup=client_awaiting_payment_kb(order_id, show_paid_btn=False),
                )
            else:
                await bot.send_message(
                    client.telegram_id,
                    f"🔄 Оператор предлагает новую сумму по заявке №{order_id}: {_money(amount)}\n"
                    "⏳ Ожидайте обновлённые реквизиты для оплаты",
                    reply_markup=client_awaiting_payment_kb(order_id, show_paid_btn=True),
                )
        except Exception:
            pass

    await message.answer(
        f"✅ Встречное предложение {_money(amount)} по заявке №{order_id} отправлено клиенту\n"
        "Отправьте клиенту обновлённые реквизиты для оплаты",
        reply_markup=send_requisites_kb(order_id),
    )


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
    await order_repo.cancel(order, "operator")
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
