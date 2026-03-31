import logging
from decimal import Decimal

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.order import Order, OrderStatus
from app.db.models.order_log import OrderLogAction
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

        await self.bot.send_message(
            settings.operator_group_id,
            "📋 Выберите действие:",
            reply_markup=operator_group_kb(),
        )

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
        """operator_id is DB user id (FK in bids). NOT telegram_id."""
        order_repo = OrderRepo(self.session)

        # Re-read with lock to prevent race between bid and auction close
        order = await order_repo.get_by_id_for_update(order_id)
        operator_user = await UserRepo(self.session).get_by_id(operator_id)
        operator_tg_id = operator_user.telegram_id if operator_user else None

        if not order or order.status != OrderStatus.pending:
            if operator_tg_id:
                await self.bot.send_message(operator_tg_id, "⏰ Аукцион по этой заявке уже завершён")
            return

        # Guard: operator cannot bid on their own order (if admin also has client role)
        if order.client_id == operator_id:
            if operator_tg_id:
                await self.bot.send_message(operator_tg_id, "⚠️ Нельзя подавать ставку на собственную заявку")
            return

        bid_repo = BidRepo(self.session)
        existing = await bid_repo.get_operator_bid(order_id, operator_id)
        if existing:
            existing.amount = amount
            await self.session.flush()
        else:
            await bid_repo.create(order_id=order_id, operator_id=operator_id, amount=amount)

        await order_repo.add_log(
            order_id=order_id,
            actor_id=operator_id,
            action=OrderLogAction.bid_placed,
            detail=f"{amount} ₽",
        )

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

    async def close_auction(self, order_id: int, actor_id: int | None = None) -> None:
        order_repo = OrderRepo(self.session)

        # Use SELECT FOR UPDATE to prevent concurrent close (scheduler + admin + auto-close race)
        order = await order_repo.get_by_id_for_update(order_id)

        if not order:
            logger.warning("close_auction: order #%d not found", order_id)
            return

        if order.status != OrderStatus.pending:
            logger.info("close_auction: order #%d already closed (%s)", order_id, order.status)
            return

        _remove_scheduler_job(order_id)

        bid_repo = BidRepo(self.session)
        min_bid = await bid_repo.get_min_bid(order_id)

        if min_bid is None:
            await order_repo.update_status(order, OrderStatus.cancelled)
            order.cancelled_by = "system"
            await self.session.flush()
            await order_repo.add_log(
                order_id=order_id, actor_id=actor_id,
                action=OrderLogAction.cancelled,
                detail="No bids after auction end",
            )
            client = await UserRepo(self.session).get_by_id(order.client_id)
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"😔 К сожалению, по вашей заявке №{order_id} не поступило ни одной ставки\n"
                        "Попробуйте создать новую заявку",
                    )
                except Exception:
                    pass
            try:
                await self.bot.send_message(
                    settings.admin_telegram_id,
                    f"⚠️ Заявка №{order_id} отменена — нет ставок за 120 мин",
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
        await order_repo.add_log(
            order_id=order_id,
            actor_id=actor_id,
            action=OrderLogAction.operator_assigned,
            detail=f"operator_id={min_bid.operator_id}, amount={min_bid.amount}",
        )
        await order_repo.add_log(
            order_id=order_id,
            actor_id=actor_id,
            action=OrderLogAction.auction_closed,
        )

        # Notify losing operators
        losers = await bid_repo.get_losers(order_id, min_bid.operator_id)
        for loser_bid in losers:
            loser = await UserRepo(self.session).get_by_id(loser_bid.operator_id)
            if loser:
                try:
                    await self.bot.send_message(
                        loser.telegram_id,
                        f"ℹ️ Аукцион по заявке №{order_id} завершён — вы не стали исполнителем",
                    )
                except Exception:
                    pass

        # Send payment info / notification
        client = await UserRepo(self.session).get_by_id(order.client_id)
        if settings.robokassa_login:
            from app.services.payment_service import PaymentService
            payment_url = PaymentService().generate_link(
                order_id=order.id,
                amount=min_bid.amount,
                revision=order.payment_revision,
            )
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"✅ Оператор назначен по заявке №{order.id}!\n"
                        f"Сумма к оплате: {min_bid.amount} ₽\n"
                        f"💳 Оплатите по ссылке: {payment_url}",
                    )
                except Exception:
                    pass
        else:
            # Manual payment mode — notify assigned operator to send requisites
            operator = await UserRepo(self.session).get_by_id(min_bid.operator_id)
            if client:
                try:
                    await self.bot.send_message(
                        client.telegram_id,
                        f"✅ Оператор назначен по заявке №{order.id}!\n"
                        f"Сумма к оплате: {min_bid.amount} ₽\n"
                        "⏳ Ожидайте — оператор скоро отправит реквизиты для оплаты",
                    )
                except Exception:
                    pass

            if operator:
                from app.bot.keyboards.order_inline import send_requisites_kb
                try:
                    await self.bot.send_message(
                        operator.telegram_id,
                        f"🎉 Вы назначены оператором по заявке №{order.id}!\n"
                        f"Ждём оплату от клиента ({min_bid.amount} ₽)\n"
                        "📤 Нажмите кнопку, чтобы отправить клиенту реквизиты для оплаты",
                        reply_markup=send_requisites_kb(order.id),
                    )
                except Exception:
                    pass

        logger.info(
            "Order #%d assigned to operator #%d, amount=%s",
            order_id, min_bid.operator_id, min_bid.amount,
        )


# ── APScheduler job ───────────────────────────────────────────────────────────

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


async def recover_overdue_auctions(bot: Bot) -> None:
    """Called on startup — closes any auctions that expired while the app was down."""
    from app.db.engine import AsyncSessionFactory

    async with AsyncSessionFactory() as session:
        try:
            orders = await OrderRepo(session).get_overdue_pending()
            if orders:
                logger.info("Recovering %d overdue auction(s)", len(orders))
            for order in orders:
                service = AuctionService(session=session, bot=bot)
                await service.close_auction(order.id)
            if orders:
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("recover_overdue_auctions failed")


def _remove_scheduler_job(order_id: int) -> None:
    from app.scheduler.setup import scheduler
    from apscheduler.jobstores.base import JobLookupError

    try:
        scheduler.remove_job(f"auction_{order_id}")
    except JobLookupError:
        pass
