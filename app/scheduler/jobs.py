"""Recurring scheduler jobs (non-auction)."""
import logging

logger = logging.getLogger(__name__)


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

    try:
        await bot.send_message(settings.admin_telegram_id, "\n".join(lines))
        logger.info("remind_unconfirmed_payments: notified admin, %d orders", len(orders))
    except Exception:
        logger.exception("remind_unconfirmed_payments: failed to notify admin")
