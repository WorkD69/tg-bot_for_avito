import logging

from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import AsyncSessionFactory
from app.db.models.order import OrderStatus
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo
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

    # 2. Find and update order
    async with AsyncSessionFactory() as session:
        try:
            order = await OrderRepo(session).get_by_invoice_id(InvId)
            if not order:
                logger.warning("Robokassa: order not found for InvId=%s", InvId)
                return PlainTextResponse("not found", status_code=404)

            if order.status != OrderStatus.awaiting_payment:
                # Already processed (duplicate callback) — still return OK
                return PlainTextResponse(f"OK{InvId}")

            await OrderRepo(session).update_status(order, OrderStatus.in_progress)

            # Notify client and operator
            from app.bot.instance import bot

            client = await UserRepo(session).get_by_id(order.client_id)
            if client:
                try:
                    await bot.send_message(
                        client.telegram_id,
                        f"✅ Оплата получена! Заявка №{order.id} передана в работу.",
                    )
                except Exception:
                    pass

            if order.operator_id:
                operator = await UserRepo(session).get_by_id(order.operator_id)
                if operator:
                    try:
                        await bot.send_message(
                            operator.telegram_id,
                            f"💰 Клиент оплатил заявку №{order.id}. Можно приступать к работе!",
                        )
                    except Exception:
                        pass

            await session.commit()
            logger.info("Robokassa: order #%s paid, status → В работе", InvId)

        except Exception:
            await session.rollback()
            logger.exception("Robokassa callback error for InvId=%s", InvId)
            return PlainTextResponse("error", status_code=500)

    # Robokassa requires exactly this string
    return PlainTextResponse(f"OK{InvId}")
