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

    async with AsyncSessionFactory() as session:
        try:
            order_repo = OrderRepo(session)
            order = await order_repo.get_by_invoice_id(InvId)
            if not order:
                logger.warning("Robokassa: order not found for InvId=%s", InvId)
                return PlainTextResponse("not found", status_code=404)

            if order.status != OrderStatus.awaiting_payment:
                # Already processed (duplicate callback) — still return OK
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

            # Mark payment received — do NOT move to in_progress yet
            # Admin must confirm via /confirmpayment
            order.payment_received_at = datetime.now(timezone.utc)
            await session.flush()

            await order_repo.add_log(
                order_id=order.id,
                action=OrderLogAction.payment_received,
                detail=f"Robokassa callback: OutSum={OutSum}, InvId={InvId}",
            )

            # Notify admin to confirm
            from app.bot.instance import bot
            from app.config import settings
            try:
                await bot.send_message(
                    settings.admin_telegram_id,
                    f"💳 Получена оплата по заявке №{order.id}\n"
                    f"Сумма: {OutSum} ₽\n"
                    f"Подтвердить: /confirmpayment {order.id}",
                )
            except Exception:
                pass

            await session.commit()
            logger.info("Robokassa: payment received for order #%s, awaiting admin confirmation", order.id)

        except Exception:
            await session.rollback()
            logger.exception("Robokassa callback error for InvId=%s", InvId)
            return PlainTextResponse("error", status_code=500)

    # Robokassa requires exactly this string
    return PlainTextResponse(f"OK{InvId}")
