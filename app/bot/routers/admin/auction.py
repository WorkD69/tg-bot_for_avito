from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.instance import bot
from app.repositories.order_repo import OrderRepo

router = Router()


@router.message(Command("endauction"), IsAdmin())
async def cmd_end_auction(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /endauction {order_id}")
        return

    order_id = int(args[1].strip())
    order = await OrderRepo(session).get_by_id(order_id, load_relations=True)
    if not order:
        await message.answer(f"Заявка №{order_id} не найдена.")
        return

    from app.db.models.order import OrderStatus
    if order.status != OrderStatus.pending:
        await message.answer(
            f"Заявка №{order_id} не на аукционе (статус: {order.status.value})."
        )
        return

    from app.services.auction_service import AuctionService
    auction = AuctionService(session=session, bot=bot)
    await auction.close_auction(order_id)
    await message.answer(f"✅ Аукцион по заявке №{order_id} принудительно завершён.")
