import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import settings
from app.db.models.order import Order
from app.db.models.user import User

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _send(self, chat_id: int, text: str, **kwargs) -> bool:
        """Safe send — swallows Forbidden/BadRequest, returns success flag."""
        try:
            await self.bot.send_message(chat_id, text, **kwargs)
            return True
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.warning("Cannot send to %d: %s", chat_id, e)
            return False

    # ── Client notifications ──────────────────────────────────────────────────

    async def order_created(self, client: User, order: Order) -> None:
        await self._send(
            client.telegram_id,
            f"✅ Заявка №{order.id} создана! Операторы получили уведомление.",
        )

    async def operator_assigned(self, client: User, order: Order, payment_url: str) -> None:
        await self._send(
            client.telegram_id,
            f"✅ Оператор назначен по заявке №{order.id}!\n"
            f"Сумма к оплате: {order.payment_amount} ₽\n"
            f"Оплатите по ссылке: {payment_url}",
        )

    async def order_paid(self, client: User, order: Order) -> None:
        await self._send(
            client.telegram_id,
            f"✅ Оплата получена! Заявка №{order.id} передана в работу.",
        )

    async def order_cancelled_no_bids(self, client: User, order: Order) -> None:
        await self._send(
            client.telegram_id,
            f"К сожалению, из-за большой нагружённости у операторов нет "
            f"возможности выполнить вашу работу (заявка №{order.id}).",
        )

    async def solution_uploaded(self, client: User, order: Order) -> None:
        await self._send(
            client.telegram_id,
            f"✅ Оператор загрузил решение по заявке №{order.id}. "
            "Посмотрите в разделе «История заявок».",
        )

    # ── Operator notifications ────────────────────────────────────────────────

    async def operator_got_order(self, operator: User, order: Order) -> None:
        await self._send(
            operator.telegram_id,
            f"🎉 Вы назначены оператором по заявке №{order.id}!\n"
            f"Ждём оплату от клиента ({order.payment_amount} ₽).",
        )

    async def operator_order_paid(self, operator: User, order: Order) -> None:
        await self._send(
            operator.telegram_id,
            f"💰 Клиент оплатил заявку №{order.id}. Можно приступать к работе!",
        )

    async def bid_accepted(self, operator: User, order_id: int, amount: Decimal) -> None:
        await self._send(
            operator.telegram_id,
            f"✅ Ставка {amount} ₽ по заявке №{order_id} принята.",
        )

    # ── Admin notifications ───────────────────────────────────────────────────

    async def admin_no_bids(self, order: Order) -> None:
        await self._send(
            settings.admin_telegram_id,
            f"⚠️ Заявка №{order.id} отменена — нет ставок за 120 мин.",
        )

    async def admin_new_review(self, reviewer: User, order_id: int, review_id: int, text: str) -> None:
        from app.bot.keyboards.admin_inline import review_moderation_kb

        name = f"@{reviewer.username}" if reviewer.username else reviewer.full_name
        await self._send(
            settings.admin_telegram_id,
            f"📝 Новый отзыв от {name} по заявке №{order_id}:\n\n{text}",
            reply_markup=review_moderation_kb(review_id),
        )
