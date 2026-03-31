from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.db.models.user import UserRole
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

router = Router()


@router.message(Command("addoperator"), IsAdmin())
async def cmd_add_operator(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /addoperator @username или /addoperator {telegram_id}")
        return

    target = args[1].strip()
    repo = UserRepo(session)

    if target.startswith("@"):
        user = await repo.get_by_username(target.lstrip("@"))
    elif target.lstrip("-").isdigit():
        user = await repo.get_by_telegram_id(int(target))
    else:
        await message.answer("⚠️ Укажите @username или telegram_id")
        return

    if not user:
        await message.answer("❌ Пользователь не найден — он должен сначала написать боту")
        return

    if user.role == UserRole.operator:
        await message.answer(f"ℹ️ {user.full_name} уже является оператором")
        return

    await repo.set_role(user, UserRole.operator)
    await message.answer(f"✅ {user.full_name} назначен оператором")


@router.message(Command("deleteoperator"), IsAdmin())
async def cmd_delete_operator(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /deleteoperator @username")
        return

    target = args[1].strip()
    repo = UserRepo(session)

    if target.startswith("@"):
        user = await repo.get_by_username(target.lstrip("@"))
    elif target.lstrip("-").isdigit():
        user = await repo.get_by_telegram_id(int(target))
    else:
        await message.answer("⚠️ Укажите @username или telegram_id")
        return

    if not user:
        await message.answer("❌ Пользователь не найден")
        return

    if user.role != UserRole.operator:
        await message.answer(f"ℹ️ {user.full_name} не является оператором")
        return

    await repo.set_role(user, UserRole.client)
    await message.answer(f"✅ {user.full_name} переведён обратно в клиенты")


@router.message(Command("operators"), IsAdmin())
async def cmd_operators(message: Message, session: AsyncSession):
    operators = await UserRepo(session).get_by_role(UserRole.operator)
    if not operators:
        await message.answer("📭 Нет операторов")
        return
    lines = ["👷 Операторы:"]
    for u in operators:
        mention = f"@{u.username}" if u.username else u.full_name
        lines.append(f"  • {mention} (id: {u.telegram_id})")
    await message.answer("\n".join(lines))


@router.message(Command("admins"), IsAdmin())
async def cmd_admins(message: Message, session: AsyncSession):
    admins = await UserRepo(session).get_by_role(UserRole.admin)
    if not admins:
        await message.answer("📭 Нет администраторов")
        return
    lines = ["👑 Администраторы:"]
    for u in admins:
        mention = f"@{u.username}" if u.username else u.full_name
        lines.append(f"  • {mention} (id: {u.telegram_id})")
    await message.answer("\n".join(lines))


@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message, session: AsyncSession):
    stats = await OrderRepo(session).get_stats()
    if not stats:
        await message.answer("📭 Заявок пока нет")
        return

    lines = ["📊 Статистика заявок:"]
    for status, count in stats.items():
        lines.append(f"  {status}: {count}")
    await message.answer("\n".join(lines))


@router.message(Command("endauction"), IsAdmin())
async def cmd_end_auction(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /endauction {order_id}")
        return

    order_id = int(args[1].strip())
    from app.services.auction_service import AuctionService
    from app.bot.instance import bot

    auction = AuctionService(session=session, bot=bot)
    await auction.close_auction(order_id)
    await message.answer(f"✅ Аукцион по заявке №{order_id} завершён")


@router.message(Command("confirmpayment"), IsAdmin())
async def cmd_confirm_payment(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /confirmpayment {order_id}")
        return

    order_id = int(args[1].strip())
    order = await OrderRepo(session).get_by_id(order_id)
    if not order:
        await message.answer("❌ Заявка не найдена")
        return

    from app.db.models.order import OrderStatus
    if order.status != OrderStatus.awaiting_payment:
        await message.answer(f"⚠️ Заявка №{order_id} не ожидает оплаты (статус: {order.status.value})")
        return

    await OrderRepo(session).update_status(order, OrderStatus.in_progress)

    from app.bot.instance import bot
    from app.repositories.user_repo import UserRepo

    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        try:
            await bot.send_message(
                client.telegram_id,
                f"✅ Оплата по заявке №{order_id} подтверждена — работа начата",
            )
        except Exception:
            pass

    if order.operator_id:
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            try:
                await bot.send_message(
                    operator.telegram_id,
                    f"✅ Оплата по заявке №{order_id} получена — приступайте к работе",
                )
            except Exception:
                pass

    await message.answer(f"✅ Заявка №{order_id} переведена в статус «В работе»")


@router.message(Command("commands"), IsAdmin())
async def cmd_commands(message: Message):
    text = (
        "📋 Команды администратора:\n\n"
        "/addoperator @username — назначить оператора\n"
        "/deleteoperator @username — снять оператора\n"
        "/operators — список всех операторов\n"
        "/admins — список всех администраторов\n"
        "/stats — статистика заявок\n"
        "/endauction {id} — завершить аукцион досрочно\n"
        "/confirmpayment {id} — подтвердить оплату вручную\n"
        "/commands — этот список"
    )
    await message.answer(text)
