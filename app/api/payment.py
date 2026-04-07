import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse

from app.db.engine import AsyncSessionFactory
from app.db.models.order import OrderStatus
from app.db.models.order_log import OrderLogAction
from app.repositories.order_repo import OrderRepo
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/payment/robokassa", response_class=PlainTextResponse)
async def robokassa_callback(
    OutSum: str = Form(...),
    InvId: str = Form(...),
    SignatureValue: str = Form(...),
):
    # 1. Verify signature
    if not PaymentService().verify_callback(OutSum, InvId, SignatureValue):
        logger.warning("Robokassa: invalid signature for InvId=%s", InvId)
        return PlainTextResponse("bad sign", status_code=400)

    admin_notify_text: str | None = None
    admin_notify_order_id: int | None = None

    async with AsyncSessionFactory() as session:
        try:
            order_repo = OrderRepo(session)
            order = await order_repo.get_by_invoice_id(InvId)
            if not order:
                # Check if this is a stale callback for an old payment_invoice_id.
                # invoice_id format: "{order_id}_{payment_revision}".
                # If the order exists but its revision has advanced, Robokassa is
                # retrying an outdated link — acknowledge to stop retries.
                parts = InvId.split("_", 1)
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    stale_order = await order_repo.get_by_id(int(parts[0]))
                    cb_revision = int(parts[1])
                    if stale_order and cb_revision < stale_order.payment_revision:
                        logger.info(
                            "Robokassa: stale InvId=%s (callback revision=%s < current=%s) — acknowledging to stop retries",
                            InvId, cb_revision, stale_order.payment_revision,
                        )
                        return PlainTextResponse(f"OK{InvId}")
                logger.warning("Robokassa: order not found for InvId=%s", InvId)
                return PlainTextResponse("not found", status_code=404)

            if order.status != OrderStatus.awaiting_payment:
                # Already processed (status advanced past awaiting_payment) — idempotent OK
                logger.info(
                    "Robokassa: duplicate callback for InvId=%s, status=%s — no-op",
                    InvId, order.status,
                )
                return PlainTextResponse(f"OK{InvId}")

            # Idempotency: payment_received_at already set means we already processed
            # this callback (Robokassa retries are common). Return OK without re-notifying.
            if order.payment_received_at:
                logger.info(
                    "Robokassa: duplicate callback for InvId=%s — payment_received_at already set, no-op",
                    InvId,
                )
                return PlainTextResponse(f"OK{InvId}")

            # Verify amount matches expected payment_amount
            try:
                from decimal import Decimal
                received = Decimal(OutSum)
                if order.payment_amount and abs(received - order.payment_amount) > Decimal("0.01"):
                    logger.warning(
                        "Robokassa: amount mismatch for order #%s: expected %s got %s",
                        order.id, order.payment_amount, OutSum,
                    )
                    return PlainTextResponse("amount mismatch", status_code=400)
            except Exception:
                pass

            # Mark payment received — do NOT move to in_progress yet.
            # Admin confirms via /confirmpayment (manual confirmation model).
            order.payment_received_at = datetime.now(timezone.utc)
            await session.flush()

            await order_repo.add_log(
                order_id=order.id,
                action=OrderLogAction.payment_received,
                detail=f"Robokassa callback: OutSum={OutSum}, InvId={InvId}",
            )

            # Collect notification data before commit (don't touch bot inside session)
            admin_notify_order_id = order.id
            admin_notify_text = (
                f"💳 Получена оплата по заявке №{order.id}\n"
                f"Сумма: {OutSum} ₽\n"
                f"Подтвердить: /confirmpayment {order.id}"
            )

            await session.commit()
            logger.info(
                "Robokassa: payment received for order #%s, awaiting admin confirmation", order.id
            )

        except Exception:
            await session.rollback()
            logger.exception("Robokassa callback error for InvId=%s", InvId)
            return PlainTextResponse("error", status_code=500)

    # Notify admin AFTER commit — so admin sees a committed state
    if admin_notify_text and admin_notify_order_id is not None:
        from app.bot.instance import bot
        from app.config import settings
        try:
            await bot.send_message(settings.admin_telegram_id, admin_notify_text)
        except Exception:
            logger.exception(
                "Robokassa: failed to notify admin for order #%d", admin_notify_order_id
            )

    # Robokassa requires exactly this string
    return PlainTextResponse(f"OK{InvId}")
