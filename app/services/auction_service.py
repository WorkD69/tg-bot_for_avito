import logging
from decimal import Decimal
from enum import Enum

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.order import Order, OrderStatus
from app.db.models.order_log import OrderLogAction
from app.repositories.bid_repo import BidRepo
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

logger = logging.getLogger(__name__)


class AuctionCloseResult(str, Enum):
    """Outcome of a close_auction call — lets callers respond accurately."""
    closed = "closed"                       # auction ended, operator assigned
    cancelled_no_bids = "cancelled_no_bids" # auction ended, no bids → order cancelled
    already_closed = "already_closed"       # order was not pending (idempotent no-op)
    not_found = "not_found"                 # order_id does not exist


class AuctionService:
    """All DB mutations happen before notifications.
    Notifications are deferred: if `deferred` list is provided they are collected
    there and the caller fires them after session.commit().
    If no deferred list is provided the notification is awaited immediately
    (only acceptable in contexts where commit already happened, e.g. tests).
    """

    def __init__(self, session: AsyncSession, bot: Bot, deferred: list | None = None) -> None:
        self.session = session
        self.bot = bot
        # Callers pass data["post_commit"] (middleware) or a local list (scheduler).
        # If None: notifications fire immediately — no post-commit safety guarantee.
        self._deferred = deferred

    def _defer(self, coro) -> None:
        """Queue a notification coroutine to run after commit."""
        if self._deferred is not None:
            self._deferred.append(coro)
        else:
            # No deferred list: fire immediately (caller is responsible for commit order)
            import asyncio
            asyncio.create_task(self._safe_run(coro))

    @staticmethod
    async def _safe_run(coro) -> None:
        try:
            await coro
        except Exception:
            logger.exception("AuctionService: notification failed")

    # ── Start auction ─────────────────────────────────────────────────────────

    async def start_auction(self, order: Order) -> None:
        from app.bot.keyboards.order_inline import group_new_order_kb
        from app.bot.keyboards.operator_reply import operator_group_kb

        # Must await this one synchronously: we need msg.message_id to persist.
        # This is an acceptable pre-commit send — if commit later fails, the group
        # message is a phantom but the order creation rolled back (very rare edge case).
        msg = await self.bot.send_message(
            settings.operator_group_id,
            f"🆕 Новая заявка №{order.id} создана",
            reply_markup=group_new_order_kb(order.id),
        )
        await OrderRepo(self.session).set_group_message_id(order, msg.message_id)

        # Reply keyboard does not need a return value — defer it
        self._defer(
            self.bot.send_message(
                settings.operator_group_id,
                "📋 Выберите действие:",
                reply_markup=operator_group_kb(),
            )
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

        # Lock the order row to prevent race between bid and auction close
        order = await order_repo.get_by_id_for_update(order_id)
        operator_user = await UserRepo(self.session).get_by_id(operator_id)
        operator_tg_id = operator_user.telegram_id if operator_user else None

        if not order or order.status != OrderStatus.pending:
            if operator_tg_id:
                self._defer(self.bot.send_message(
                    operator_tg_id, "⏰ Аукцион по этой заявке уже завершён"
                ))
            return

        # Guard: operator cannot bid on own order
        if order.client_id == operator_id:
            if operator_tg_id:
                self._defer(self.bot.send_message(
                    operator_tg_id, "⚠️ Нельзя подавать ставку на собственную заявку"
                ))
            return

        # Atomic upsert — safe against parallel bids from same operator
        bid_repo = BidRepo(self.session)
        await bid_repo.upsert(order_id=order_id, operator_id=operator_id, amount=amount)

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
            self._defer(self.bot.send_message(
                operator_tg_id,
                f"✅ Ставка {amount} ₽ по заявке №{order_id} принята\n\n" + card_text,
                reply_markup=free_order_card_kb(order_id),
            ))

        # Auto-assign if bid exactly matches client budget (exact-budget rule)
        if order.budget is not None and amount == order.budget:
            logger.info("Auto-assign triggered for order #%d, operator #%d", order_id, operator_id)
            await self.close_auction(order_id)

    # ── Close auction ─────────────────────────────────────────────────────────

    async def close_auction(self, order_id: int, actor_id: int | None = None) -> AuctionCloseResult:
        """Single source of truth for auction close.

        All decision-making happens under SELECT FOR UPDATE — no external pre-checks needed.
        Returns AuctionCloseResult so callers can give accurate feedback without second-guessing.
        All bot.send_message calls are deferred via _defer() — fire after caller commits.

        Winner selection rules (preserved):
        - Lowest amount wins.
        - Tie-break: earliest created_at wins (BidRepo.get_min_bid guarantees this).
        - Exact-budget auto-close: triggered from place_bid, same path.
        """
        order_repo = OrderRepo(self.session)

        # Lock row — prevents concurrent close (scheduler + admin + auto-close race)
        order = await order_repo.get_by_id_for_update(order_id)

        if not order:
            logger.warning("close_auction: order #%d not found", order_id)
            return AuctionCloseResult.not_found

        if order.status != OrderStatus.pending:
            logger.info(
                "close_auction: order #%d already closed (%s) — idempotent no-op",
                order_id, order.status,
            )
            return AuctionCloseResult.already_closed

        _remove_scheduler_job(order_id)

        bid_repo = BidRepo(self.session)
        # get_min_bid: ORDER BY amount ASC, created_at ASC → tie-break by earliest bid
        min_bid = await bid_repo.get_min_bid(order_id)

        if min_bid is None:
            await order_repo.cancel(order, "system")
            await order_repo.add_log(
                order_id=order_id, actor_id=actor_id,
                action=OrderLogAction.cancelled,
                detail="No bids after auction end",
            )
            client = await UserRepo(self.session).get_by_id(order.client_id)
            if client:
                self._defer(self.bot.send_message(
                    client.telegram_id,
                    f"😔 К сожалению, по вашей заявке №{order_id} не поступило ни одной ставки\n"
                    "Попробуйте создать новую заявку",
                ))
            self._defer(self.bot.send_message(
                settings.admin_telegram_id,
                f"⚠️ Заявка №{order_id} отменена — нет ставок за 120 мин",
            ))
            logger.info("Order #%d cancelled — no bids", order_id)
            return AuctionCloseResult.cancelled_no_bids

        # Assign operator with lowest bid (tie-break: earliest)
        await order_repo.assign_operator(
            order=order,
            operator_id=min_bid.operator_id,
            payment_amount=min_bid.amount,
        )
        await order_repo.add_log(
            order_id=order_id, actor_id=actor_id,
            action=OrderLogAction.operator_assigned,
            detail=f"operator_id={min_bid.operator_id}, amount={min_bid.amount}",
        )
        await order_repo.add_log(
            order_id=order_id, actor_id=actor_id,
            action=OrderLogAction.auction_closed,
        )

        # Notify losing operators
        losers = await bid_repo.get_losers(order_id, min_bid.operator_id)
        for loser_bid in losers:
            loser = await UserRepo(self.session).get_by_id(loser_bid.operator_id)
            if loser:
                self._defer(self.bot.send_message(
                    loser.telegram_id,
                    f"ℹ️ Аукцион по заявке №{order_id} завершён — вы не стали исполнителем",
                ))

        # Notify client and winner operator
        client = await UserRepo(self.session).get_by_id(order.client_id)
        if settings.robokassa_login:
            from app.services.payment_service import PaymentService
            payment_url = PaymentService().generate_link(
                invoice_id=order.payment_invoice_id,
                amount=order.payment_amount,
            )
            if client:
                self._defer(self.bot.send_message(
                    client.telegram_id,
                    f"✅ Оператор назначен по заявке №{order.id}!\n"
                    f"Сумма к оплате: {min_bid.amount} ₽\n"
                    f"💳 Оплатите по ссылке: {payment_url}",
                ))
        else:
            # Manual payment mode — operator sends requisites
            operator = await UserRepo(self.session).get_by_id(min_bid.operator_id)
            if client:
                self._defer(self.bot.send_message(
                    client.telegram_id,
                    f"✅ Оператор назначен по заявке №{order.id}!\n"
                    f"Сумма к оплате: {min_bid.amount} ₽\n"
                    "⏳ Ожидайте — оператор скоро отправит реквизиты для оплаты",
                ))
            if operator:
                from app.bot.keyboards.order_inline import send_requisites_kb
                self._defer(self.bot.send_message(
                    operator.telegram_id,
                    f"🎉 Вы назначены оператором по заявке №{order.id}!\n"
                    f"Ждём оплату от клиента ({min_bid.amount} ₽)\n"
                    "📤 Нажмите кнопку, чтобы отправить клиенту реквизиты для оплаты",
                    reply_markup=send_requisites_kb(order.id),
                ))

        logger.info(
            "Order #%d assigned to operator #%d, amount=%s",
            order_id, min_bid.operator_id, min_bid.amount,
        )
        return AuctionCloseResult.closed


# ── APScheduler job ───────────────────────────────────────────────────────────

async def _auto_close_auction(order_id: int) -> None:
    """Called by APScheduler — opens its own DB session.
    Commit happens before notifications (post-commit safety guaranteed).
    """
    from app.db.engine import AsyncSessionFactory
    from app.bot.instance import bot

    deferred: list = []
    async with AsyncSessionFactory() as session:
        try:
            service = AuctionService(session=session, bot=bot, deferred=deferred)
            await service.close_auction(order_id)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("_auto_close_auction failed for order #%d", order_id)
            return

    # Notifications fire after commit
    for coro in deferred:
        try:
            await coro
        except Exception:
            logger.exception("_auto_close_auction: notification failed for order #%d", order_id)


async def recover_overdue_auctions(bot: Bot) -> None:
    """Called on startup — closes any auctions that expired while the app was down.

    Each order is processed in its own session so a single failure does not
    prevent other overdue auctions from being recovered.
    """
    from app.db.engine import AsyncSessionFactory

    # Collect overdue IDs in a short-lived read session
    async with AsyncSessionFactory() as session:
        try:
            orders = await OrderRepo(session).get_overdue_pending()
            order_ids = [o.id for o in orders]
        except Exception:
            logger.exception("recover_overdue_auctions: failed to fetch overdue orders")
            return

    if not order_ids:
        return

    logger.info("Recovering %d overdue auction(s): %s", len(order_ids), order_ids)

    # Process each order in isolation — one failure does not affect others
    for order_id in order_ids:
        deferred: list = []
        async with AsyncSessionFactory() as session:
            try:
                service = AuctionService(session=session, bot=bot, deferred=deferred)
                result = await service.close_auction(order_id)
                await session.commit()
                logger.info("Recovered order #%d: %s", order_id, result)
            except Exception:
                await session.rollback()
                logger.exception("recover_overdue_auctions: failed for order #%d", order_id)
                continue

        # Notifications after commit, outside the session context
        for coro in deferred:
            try:
                await coro
            except Exception:
                logger.exception("recover notify failed for order #%d", order_id)


def _remove_scheduler_job(order_id: int) -> None:
    from app.scheduler.setup import scheduler
    from apscheduler.jobstores.base import JobLookupError

    try:
        scheduler.remove_job(f"auction_{order_id}")
    except JobLookupError:
        pass
