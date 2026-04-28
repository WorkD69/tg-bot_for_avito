"""Recurring scheduler jobs (non-auction) and growth follow-up jobs."""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ── Follow-up after order completion ─────────────────────────────────────────


def schedule_followup_job(order_id: int) -> None:
    """Schedule a follow-up message to the client 24 hours after order completion.

    Uses replace_existing=True — safe to call multiple times for the same order_id.
    The job itself is idempotent (checks followup_sent_at before sending).
    """
    from app.scheduler.setup import scheduler

    run_at = datetime.now(timezone.utc) + timedelta(hours=24)
    try:
        scheduler.add_job(
            send_order_followup,
            trigger="date",
            run_date=run_at,
            args=[order_id],
            id=f"followup_{order_id}",
            replace_existing=True,
        )
        logger.info("schedule_followup_job: scheduled for order #%d at %s", order_id, run_at.isoformat())
    except Exception:
        # Non-fatal: main order flow must not be interrupted by scheduler issues
        logger.warning(
            "schedule_followup_job: failed to schedule for order #%d",
            order_id,
            exc_info=True,
        )


async def send_order_followup(order_id: int) -> None:
    """Send a follow-up message to the client 24h after order completion.

    Idempotency: atomically sets followup_sent_at before sending.
    If followup_sent_at is already set — silently skips (no double-send).

    Guards applied before sending:
      1. Order must still be in completed status with followup_sent_at=NULL.
      2. Client has no active orders (pending/awaiting_payment/in_progress) —
         they're already engaged, no need to nudge.
      3. Whether client already left a review — affects message text.
    """
    from app.db.engine import AsyncSessionFactory
    from app.db.models.order import Order, OrderStatus
    from app.db.models.review import Review
    from app.bot.instance import bot
    from app.repositories.user_repo import UserRepo
    from sqlalchemy import func, select, update

    async with AsyncSessionFactory() as session:
        # Step 1: atomically claim the followup slot
        result = await session.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.status == OrderStatus.completed,
                Order.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=datetime.now(timezone.utc))
            .returning(Order.client_id)
        )
        row = result.first()
        if row is None:
            logger.info(
                "send_order_followup: skip order #%d (already sent or not in completed state)",
                order_id,
            )
            await session.rollback()
            return

        client_id: int = row[0]

        # Step 2: guard — client has active orders (already engaged with bot)
        active_count = await session.scalar(
            select(func.count(Order.id)).where(
                Order.client_id == client_id,
                Order.status.in_([
                    OrderStatus.pending,
                    OrderStatus.awaiting_payment,
                    OrderStatus.in_progress,
                ]),
            )
        )

        # Step 3: check review status (affects message copy, not a hard guard)
        has_review = bool(await session.scalar(
            select(func.count(Review.id)).where(Review.order_id == order_id)
        ))

        # Step 4: load client
        client = await UserRepo(session).get_by_id(client_id)

        await session.commit()

    if not client:
        logger.warning("send_order_followup: client #%d not found for order #%d", client_id, order_id)
        return

    if active_count:
        logger.info(
            "send_order_followup: skip order #%d — client #%d has %d active order(s)",
            order_id, client_id, active_count,
        )
        return

    # Build message
    first_name = client.full_name.split()[0] if client.full_name else client.full_name
    lines = [
        f"👋 {first_name}, надеемся, задача №{order_id} выполнена отлично!",
        "",
        "Если появятся ещё задачи — мы здесь 📋 Просто нажмите «Создать заявку»",
    ]
    if not has_review:
        lines += [
            "",
            f"Кстати, будем рады вашему отзыву — откройте «История заявок» → заявка №{order_id} → «⭐ Оставить отзыв»",
        ]

    try:
        await bot.send_message(client.telegram_id, "\n".join(lines))
        logger.info(
            "send_order_followup: sent to client #%d (tg=%d) for order #%d",
            client_id, client.telegram_id, order_id,
        )
    except Exception:
        # followup_sent_at is already set → no retry → no spam even on network error
        logger.warning(
            "send_order_followup: failed to send message to client #%d for order #%d",
            client_id, order_id,
            exc_info=True,
        )


async def remind_unconfirmed_payments() -> None:
    """Daily job: notify admin about payments received but not yet confirmed.

    Fires once per day. Covers the edge case where Robokassa callback succeeded
    but the admin notification (bot.send_message) silently failed after commit.
    """
    from app.db.engine import AsyncSessionFactory
    from app.repositories.order_repo import OrderRepo
    from app.bot.instance import bot
    from app.config import settings
    from app.bot.formatters import _money

    async with AsyncSessionFactory() as session:
        orders = await OrderRepo(session).get_unconfirmed_payments()

    if not orders:
        return

    lines = [f"⚠️ Ожидают подтверждения оплаты ({len(orders)} заявок):"]
    for o in orders:
        lines.append(
            f"  • №{o.id} — {_money(o.payment_amount)} — /confirmpayment {o.id}"
        )

    from app.db.models.user import UserRole
    from app.repositories.user_repo import UserRepo
    try:
        async with AsyncSessionFactory() as _s:
            admins = await UserRepo(_s).get_by_role(UserRole.admin)
        for admin in admins:
            await bot.send_message(admin.telegram_id, "\n".join(lines))
        logger.info("remind_unconfirmed_payments: notified %d admins, %d orders", len(admins), len(orders))
    except Exception:
        logger.exception("remind_unconfirmed_payments: failed to notify admins")
