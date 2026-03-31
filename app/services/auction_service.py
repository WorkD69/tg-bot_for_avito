import logging
from decimal import Decimal

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.order import Order, OrderStatus
from app.repositories.bid_repo import BidRepo
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

logger = logging.getLogger(__name__)


class AuctionService:
    def __init__(self, session: AsyncSession, bot: Bot) -> None:
        self.session = session
        self.bot = bot

    # ── Start auction ─────────────────────────────────────────────────────────

    async def start_auction(self, order: Order) -> None:
        from app.bot.keyboards.order_inline import group_new_order_kb
        from app.bot.keyboards.operator_reply import operator_group_kb

        msg = await self.bot.send_message(
            settings.operator_group_id,
            f"🆕 Новая заявка №{order.id} создана",
            reply_markup=group_new_order_kb(order.id),
        )
        await OrderRepo(self.session).set_group_message_id(order, msg.message_id)

        # Send reply keyboard so operators see action buttons
        await self.bot.send_message(
            settings.operator_group_id,
            "📋 Выберите действие:",
            reply_markup=operator_group_kb(),
        )

        # Schedule auto-close job
        from app.scheduler.setup import scheduler

        scheduler.add_job(
            _auto_close_auction,
            "date",
            run_date=order.auction_end_at,
            args=[order.id],
            id=f"auction_{order.id}",
            replace_existing=True,
        )
        logger.info("Auction started for order #%d, ends at %s", order.id, order.auction_end_at)

    # ── Place bid ─────────────────────────────────────────────────────────────

    async def place_bid(self, order_id: int, operator_id: int, amount: Decimal) -> None:
        """operator_id is DB user id (foreign key in bids table)."""
        order_repo = OrderRepo(self.session)
        order = await order_repo.get_by_id(order_id, load_relations=True)

        # Resolve telegram_id for sending messages
        operator_user = await UserRepo(self.session).get_by_id(operator_id)
        operator_tg_id = operator_user.telegram_id if operator_user else None

        if not order or order.status != OrderStatus.pending:
            if operator_tg_id:
                await self.bot.send_message(operator_tg_id, "⏰ Аукцион по этой заявке уже завершён")
            return

        bid_repo = BidRepo(self.session)

        # Update existing bid or create new one
        existing = await bid_repo.get_operator_bid(order_id, operator_id)
        if existing:
            existing.amount = amount
            await self.session.flush()
        else:
            await bid_repo.create(order_id=order_id, operator_id=operator_id, amount=amount)

        # Expire session cache so reload picks up the new bid
        self.session.expire_all()
        order = await order_repo.get_by_id(order_id, load_relations=True)

        if operator_tg_id:
            from app.bot.formatters import format_order_card
            from app.bot.keyboards.order_inline import free_order_card_kb
            card_text = format_order_card(order)
            await self.bot.send_message(
                operator_tg_id,
                f"✅ Ставка {amount} ₽ по заявке №{order_id} принята\n\n" + card_text,
                reply_markup=free_order_card_kb(order_id),
            )

        # Auto-assign if bid matches client budget exactly
        if order.budget is not None and amount == order.budget:
            logger.info("Auto-assign triggered for order #%d, operator #%d", order_id, operator_id)
            await self.close_auction(order_id)

    # ── Close auction ─────────────────────────────────────────────────────────

    async def close_auction(self, order_id: int) -> None:
        order_repo = OrderRepo(self.session)
        order = await order_repo.get_by_id(order_id, load_relations=True)

        if not order:
            logger.warning("close_auction: order #%d not found", order_id)
            return

        if order.status != OrderStatus.pending:
            logger.info("close_auction: order #%d already closed (%s)", order_id, order.status)
            return

        # Cancel scheduled job if it still exists
        _remove_scheduler_job(order_id)

        bid_repo = BidRepo(self.session)
        min_bid = await bid_repo.get_min_bid(order_id)

        if min_bid is None:
            # No bids — cancel order
            await order_repo.update_status(order, OrderStatus.cancelled)
            client = await UserRepo(self.session).get_by_id(order.client_id)
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"К сожалению, из-за большой нагружённости у операторов нет "
                        f"возможности выполнить вашу работу (заявка №{order_id}).",
                    )
                except Exception:
                    pass

            # Notify admin
            try:
                await self.bot.send_message(
                    settings.admin_telegram_id,
                    f"⚠️ Заявка №{order_id} отменена — нет ставок за 120 мин.",
                )
            except Exception:
                pass

            logger.info("Order #%d cancelled — no bids", order_id)
            return

        # Assign operator with lowest bid
        await order_repo.assign_operator(
            order=order,
            operator_id=min_bid.operator_id,
            payment_amount=min_bid.amount,
        )

        # Generate payment link and notify client (skip if Robokassa not configured)
        client = await UserRepo(self.session).get_by_id(order.client_id)
        if settings.robokassa_login:
            from app.services.payment_service import PaymentService
            payment_url = PaymentService().generate_link(
                order_id=order.id,
                amount=min_bid.amount,
            )
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"✅ Оператор назначен по заявке №{order.id}!\n"
                        f"Сумма к оплате: {min_bid.amount} ₽\n"
                        f"Оплатите по ссылке: {payment_url}",
                    )
                except Exception:
                    pass
        else:
            # Robokassa not configured — notify admin to confirm manually
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"✅ Оператор назначен по заявке №{order.id}!\n"
                        f"Сумма к оплате: {min_bid.amount} ₽\n"
                        "⏳ Реквизиты для оплаты будут отправлены администратором",
                    )
                except Exception:
                    pass
            try:
                await self.bot.send_message(
                    settings.admin_telegram_id,
                    f"💳 Заявка №{order.id} ожидает ручного подтверждения оплаты\n"
                    f"Сумма: {min_bid.amount} ₽\n"
                    f"Подтвердить: /confirmpayment {order.id}",
                )
            except Exception:
                pass

        # Notify assigned operator
        operator = await UserRepo(self.session).get_by_id(min_bid.operator_id)
        if operator:
            try:
                await self.bot.send_message(
                    operator.telegram_id,
                    f"🎉 Вы назначены оператором по заявке №{order.id}!\n"
                    f"Ждём оплату от клиента ({min_bid.amount} ₽).",
                )
            except Exception:
                pass

        logger.info(
            "Order #%d assigned to operator #%d, amount=%s",
            order_id, min_bid.operator_id, min_bid.amount,
        )


# ── APScheduler job (standalone, opens its own session) ──────────────────────

async def _auto_close_auction(order_id: int) -> None:
    """Called by APScheduler — must open its own DB session."""
    from app.db.engine import AsyncSessionFactory
    from app.bot.instance import bot

    async with AsyncSessionFactory() as session:
        try:
            service = AuctionService(session=session, bot=bot)
            await service.close_auction(order_id)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("_auto_close_auction failed for order #%d", order_id)


def _remove_scheduler_job(order_id: int) -> None:
    from app.scheduler.setup import scheduler
    from apscheduler.jobstores.base import JobLookupError

    try:
        scheduler.remove_job(f"auction_{order_id}")
    except JobLookupError:
        pass
