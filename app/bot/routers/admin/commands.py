import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin

logger = logging.getLogger(__name__)
from app.db.models.order import OrderStatus
from app.db.models.order_log import OrderLogAction
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
        await message.answer("Использование: /deleteoperator @username или /deleteoperator {telegram_id}")
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

    # Guard: cannot remove operator with active assigned orders
    active = await OrderRepo(session).get_operator_active_orders(user.id)
    if active:
        ids = ", ".join(str(o.id) for o in active)
        await message.answer(
            f"⚠️ Нельзя удалить оператора — у него {len(active)} активных заявок: №{ids}\n"
            "Сначала переназначьте или завершите их"
        )
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
async def cmd_end_auction(message: Message, session: AsyncSession, post_commit: list):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /endauction {order_id}")
        return

    order_id = int(args[1].strip())

    from app.services.auction_service import AuctionService, AuctionCloseResult
    from app.bot.instance import bot
    from app.repositories.user_repo import UserRepo

    admin = await UserRepo(session).get_by_telegram_id(message.from_user.id)
    actor_id = admin.id if admin else None

    # No pre-check on order.status here — close_auction decides under lock.
    # Pre-checks are racy and can give misleading feedback in concurrent scenarios.
    auction = AuctionService(session=session, bot=bot, deferred=post_commit)
    result = await auction.close_auction(order_id, actor_id=actor_id)

    if result == AuctionCloseResult.not_found:
        await message.answer("❌ Заявка не найдена")
    elif result == AuctionCloseResult.already_closed:
        await message.answer(f"⚠️ Аукцион по заявке №{order_id} уже завершён — заявка не в статусе «На рассмотрении»")
    elif result == AuctionCloseResult.cancelled_no_bids:
        await message.answer(f"✅ Аукцион по заявке №{order_id} завершён — ставок не было, заявка отменена")
    else:
        await message.answer(f"✅ Аукцион по заявке №{order_id} завершён, оператор назначен")


@router.message(Command("confirmpayment"), IsAdmin())
async def cmd_confirm_payment(message: Message, session: AsyncSession, post_commit: list):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /confirmpayment {order_id}")
        return

    order_id = int(args[1].strip())
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(order_id)
    if not order:
        await message.answer("❌ Заявка не найдена")
        return

    if order.status != OrderStatus.awaiting_payment:
        await message.answer(f"⚠️ Заявка №{order_id} не ожидает оплаты (статус: {order.status.value})")
        return

    if not order.payment_received_at:
        await message.answer(
            f"⚠️ Заявка №{order_id}: оплата ещё не зафиксирована\n"
            "Дождитесь, пока клиент нажмёт «Я оплатил» или придёт callback от Robokassa"
        )
        return

    await order_repo.confirm_payment(order)

    from app.repositories.user_repo import UserRepo
    admin = await UserRepo(session).get_by_telegram_id(message.from_user.id)
    actor_id = admin.id if admin else None
    await order_repo.add_log(
        order_id=order_id, actor_id=actor_id,
        action=OrderLogAction.payment_confirmed,
    )

    from app.bot.instance import bot
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        post_commit.append(bot.send_message(
            client.telegram_id,
            f"✅ Оплата по заявке №{order_id} подтверждена — работа начата",
        ))

    if order.operator_id:
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            post_commit.append(bot.send_message(
                operator.telegram_id,
                f"✅ Оплата по заявке №{order_id} получена — приступайте к работе",
            ))

    await message.answer(f"✅ Заявка №{order_id} переведена в статус «В работе»")


@router.message(Command("completeorder"), IsAdmin())
async def cmd_complete_order(message: Message, session: AsyncSession, post_commit: list):
    """Admin can forcibly complete an order."""
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /completeorder {order_id}")
        return

    order_id = int(args[1].strip())
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(order_id)
    if not order:
        await message.answer("❌ Заявка не найдена")
        return
    if order.status != OrderStatus.in_progress:
        await message.answer(f"⚠️ Заявка №{order_id} не в работе (статус: {order.status.value})")
        return

    from app.repositories.user_repo import UserRepo
    admin = await UserRepo(session).get_by_telegram_id(message.from_user.id)
    actor_id = admin.id if admin else None

    await order_repo.update_status(order, OrderStatus.completed)
    await order_repo.add_log(
        order_id=order_id, actor_id=actor_id, action=OrderLogAction.completed,
        detail="Admin forced complete",
    )

    # Create earning record for payout tracking
    if order.payment_amount and order.operator_id:
        from app.repositories.earning_repo import EarningRepo
        from app.config import settings as _settings
        await EarningRepo(session).create_for_order(
            order_id=order_id,
            operator_id=order.operator_id,
            gross_amount=order.payment_amount,
            payout_percent=_settings.operator_payout_percent,
        )

    from app.bot.instance import bot
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        post_commit.append(bot.send_message(
            client.telegram_id,
            f"🎉 Заявка №{order_id} выполнена!\n📂 Посмотрите в разделе «История заявок»",
        ))

    await message.answer(f"✅ Заявка №{order_id} завершена")


@router.message(Command("cancelorder"), IsAdmin())
async def cmd_cancel_order(message: Message, session: AsyncSession, post_commit: list):
    """Admin can forcibly cancel an order in any non-final status."""
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Использование: /cancelorder {order_id}")
        return

    order_id = int(args[1].strip())
    order_repo = OrderRepo(session)
    order = await order_repo.get_by_id_for_update(order_id)
    if not order:
        await message.answer("❌ Заявка не найдена")
        return

    final = (OrderStatus.completed, OrderStatus.cancelled)
    if order.status in final:
        await message.answer(
            f"⚠️ Заявка №{order_id} уже в финальном статусе ({order.status.value}) — отменить нельзя"
        )
        return

    from app.repositories.user_repo import UserRepo
    admin = await UserRepo(session).get_by_telegram_id(message.from_user.id)
    actor_id = admin.id if admin else None

    await order_repo.cancel(order, "admin")
    await order_repo.add_log(
        order_id=order_id, actor_id=actor_id, action=OrderLogAction.cancelled,
        detail="Admin forced cancel",
    )

    from app.bot.instance import bot
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        post_commit.append(bot.send_message(
            client.telegram_id,
            f"❌ Заявка №{order_id} была отменена администратором",
        ))

    if order.operator_id:
        operator = await UserRepo(session).get_by_id(order.operator_id)
        if operator:
            post_commit.append(bot.send_message(
                operator.telegram_id,
                f"❌ Заявка №{order_id} была отменена администратором",
            ))

    await message.answer(f"✅ Заявка №{order_id} отменена")


@router.message(Command("addadmin"), IsAdmin())
async def cmd_add_admin(message: Message, session: AsyncSession):
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /addadmin @username или /addadmin {telegram_id}")
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

    if user.role == UserRole.admin:
        await message.answer(f"ℹ️ {user.full_name} уже является администратором")
        return

    await repo.set_role(user, UserRole.admin)
    await message.answer(f"✅ {user.full_name} назначен администратором")


@router.message(Command("removeadmin"), IsAdmin())
async def cmd_remove_admin(message: Message, session: AsyncSession):
    from app.config import settings as _settings
    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /removeadmin @username или /removeadmin {telegram_id}")
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

    if user.telegram_id == _settings.admin_telegram_id:
        await message.answer("⚠️ Нельзя снять главного администратора")
        return

    if user.telegram_id == message.from_user.id:
        await message.answer("⚠️ Нельзя снять себя")
        return

    if user.role != UserRole.admin:
        await message.answer(f"ℹ️ {user.full_name} не является администратором")
        return

    await repo.set_role(user, UserRole.client)
    await message.answer(f"✅ {user.full_name} снят с должности администратора")


@router.message(Command("commands"), IsAdmin())
async def cmd_commands(message: Message):
    text = (
        "📋 Команды администратора:\n\n"
        "— Управление ролями —\n"
        "/addoperator @username — назначить оператора\n"
        "/deleteoperator @username — снять оператора\n"
        "/addadmin @username — назначить администратора\n"
        "/removeadmin @username — снять администратора\n"
        "/operators — список всех операторов\n"
        "/admins — список всех администраторов\n\n"
        "— Заявки —\n"
        "/stats — статистика заявок по статусам\n"
        "/endauction {id} — завершить аукцион досрочно\n"
        "/confirmpayment {id} — подтвердить оплату вручную\n"
        "/completeorder {id} — принудительно завершить заявку\n"
        "/cancelorder {id} — принудительно отменить заявку\n\n"
        "— Статистика и выплаты —\n"
        "/operatorstats [@op] [7d|30d] — статистика оператора\n"
        "/payouts [@op] — pending выплаты\n"
        "/markpaid @op [примечание] — отметить выплату как произведённую\n"
        "/excludeearning {order_id} [причина] — исключить заявку из выплат\n"
        "/adjustearning {order_id} {сумма} [причина] — скорректировать сумму выплаты\n\n"
        "/commands — этот список"
    )
    await message.answer(text)
