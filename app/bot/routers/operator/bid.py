import re as _re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.bid import BidStates
from app.db.models.order import OrderStatus
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()

# Ambiguous "thousands separator" pattern: 1–3 digits, separator, exactly 3 digits
_AMBIGUOUS_BID_RE = _re.compile(r"^\d{1,3}[.,]\d{3}$")


def _parse_bid_amount(raw: str) -> Decimal:
    """Parse bid string → Decimal.

    Accepted: "1200", "1 200", "1200р", "1200₽", "1200,50", "1200.50"
    Rejected: "1,200" / "1.200" (ambiguous), empty, <=0, non-numeric
    """
    cleaned = raw.strip().rstrip("рРрубРУБ₽").strip()
    if not cleaned:
        raise ValueError("empty")
    no_spaces = cleaned.replace(" ", "")
    if _AMBIGUOUS_BID_RE.match(no_spaces):
        raise ValueError("ambiguous")
    normalized = no_spaces.replace(",", ".")
    amount = Decimal(normalized)  # raises InvalidOperation on garbage
    if amount <= 0:
        raise ValueError("non-positive")
    return amount


@router.callback_query(OrderCB.filter(F.action == "bid"), IsOperator())
async def start_bid(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    from app.bot.states.note import SolutionStates
    current_state = await state.get_state()
    if current_state == SolutionStates.waiting_files:
        await callback.answer(
            "⚠️ Загрузка решения в процессе — сначала завершите /done",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    # Guard: cannot bid on own order
    if order.client_id == user.id:
        await callback.answer("⚠️ Нельзя подавать ставку на собственную заявку", show_alert=True)
        return

    if order.operator_id is not None:
        await callback.answer("⚠️ Заявка уже взята другим оператором", show_alert=True)
        return

    if order.status != OrderStatus.pending:
        await callback.answer("⏰ Аукцион по этой заявке уже завершён", show_alert=True)
        return

    await state.set_state(BidStates.waiting_price)
    await state.update_data(order_id=order.id)
    from app.bot.formatters import _money
    await callback.message.answer(
        f"📋 Заявка №{order.id} — бюджет клиента: {_money(order.budget)}\n"
        "💰 Введите вашу ставку (число в рублях):"
    )
    await callback.answer()


@router.message(BidStates.waiting_price, F.text, IsOperator())
async def got_bid_price(
    message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list
):
    try:
        amount = _parse_bid_amount(message.text)
    except (ValueError, InvalidOperation) as e:
        reason = str(e)
        if reason == "ambiguous":
            await message.answer(
                "❌ Неоднозначный формат суммы\n"
                "Используйте: 1200 или 1200,50 (не 1,200 или 1.200)"
            )
        else:
            await message.answer(
                "❌ Введите сумму числом в рублях\n"
                "Например: 1200"
            )
        return

    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    from app.services.auction_service import AuctionService
    from app.bot.instance import bot

    # Pass post_commit so all notifications (including auto-close) fire after commit
    auction = AuctionService(session=session, bot=bot, deferred=post_commit)
    await auction.place_bid(order_id=order_id, operator_id=user.id, amount=amount)


@router.message(BidStates.waiting_price, IsOperator())
async def bid_unexpected_type(message: Message):
    """Operator sent a file or other non-text at the bid step."""
    await message.answer(
        "💰 Введите сумму ставки числом в рублях\n"
        "Например: 1200"
    )
