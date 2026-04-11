"""Admin payout management commands.

Commands:
  /operatorstats [@op|id] [7d|30d]  — operator stats + earnings summary
  /payouts [@op|id]                  — pending earnings (all or per operator)
  /markpaid @op|id [note]            — mark all pending earnings as paid
  /excludeearning {order_id} [reason]— exclude order from payout
  /adjustearning {order_id} {amount} [reason] — manually override payout amount
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.formatters import _money
from app.db.models.operator_earning import EarningStatus
from app.db.models.user import UserRole
from app.repositories.earning_repo import EarningRepo
from app.repositories.order_repo import OrderRepo
from app.repositories.user_repo import UserRepo

logger = logging.getLogger(__name__)
router = Router()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_period(token: str | None) -> datetime | None:
    """'7d' → now-7d, '30d' → now-30d, None → None (all time)."""
    if not token:
        return None
    token = token.strip().lower()
    if token == "7d":
        return datetime.now(timezone.utc) - timedelta(days=7)
    if token == "30d":
        return datetime.now(timezone.utc) - timedelta(days=30)
    if token == "today":
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


async def _resolve_operator(target: str, repo: UserRepo):
    """Resolve @username or telegram_id to a User. Returns None if not found."""
    if target.startswith("@"):
        return await repo.get_by_username(target.lstrip("@"))
    if target.lstrip("-").isdigit():
        return await repo.get_by_telegram_id(int(target))
    return None


def _mention(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


# ── /operatorstats ────────────────────────────────────────────────────────────

@router.message(Command("operatorstats"), IsAdmin())
async def cmd_operator_stats(message: Message, session: AsyncSession):
    """
    /operatorstats              — summary for ALL operators
    /operatorstats @op          — detailed stats for one operator
    /operatorstats @op 7d       — same, last 7 days
    /operatorstats @op 30d      — same, last 30 days
    """
    args = (message.text or "").split(maxsplit=3)[1:]  # drop command

    user_repo = UserRepo(session)
    earning_repo = EarningRepo(session)
    order_repo = OrderRepo(session)

    # No args → summary table for all operators
    if not args:
        operators = await user_repo.get_by_role(UserRole.operator)
        admins = await user_repo.get_by_role(UserRole.admin)
        all_ops = operators + [u for u in admins if u not in operators]
        # Actually, also show admins who have earnings (they can act as operators)
        # For simplicity: show everyone who is operator or admin
        op_list = await user_repo.get_by_role(UserRole.operator)

        if not op_list:
            await message.answer("📭 Операторов пока нет")
            return

        lines = ["📊 Статистика операторов:\n"]
        for op in op_list:
            summary = await earning_repo.get_summary_by_operator(op.id)
            active = await order_repo.get_operator_active_orders(op.id)
            mention = _mention(op)
            if summary["total_completed"] == 0:
                lines.append(
                    f"👷 {mention}\n"
                    f"  Активных заявок: {len(active)}\n"
                    f"  Завершённых: 0 — выплат нет\n"
                )
            else:
                cr = summary["total_completed"]
                pct = summary["pending_sum"] / summary["total_share"] * 100 if summary["total_share"] else Decimal(0)
                lines.append(
                    f"👷 {mention}\n"
                    f"  Активных: {len(active)}, Завершённых: {cr}\n"
                    f"  Выручка (gross): {_money(summary['total_gross'])}\n"
                    f"  К выплате: {_money(summary['total_share'])} "
                    f"(ожидает: {_money(summary['pending_sum'])}, выплачено: {_money(summary['paid_sum'])})\n"
                )
        await message.answer("\n".join(lines))
        return

    # First arg — operator target
    target = args[0]
    period_token = args[1] if len(args) > 1 else None
    since = _parse_period(period_token)

    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден — он должен сначала написать боту")
        return

    summary = await earning_repo.get_summary_by_operator(op.id, since=since)
    active = await order_repo.get_operator_active_orders(op.id)
    completed = await order_repo.get_operator_completed_orders(op.id)
    earnings = await earning_repo.get_by_operator(op.id, since=since)

    period_label = f" (за {period_token})" if period_token else " (всё время)"
    mention = _mention(op)

    lines = [f"📊 Статистика {mention}{period_label}\n"]
    lines.append(f"Активных заявок: {len(active)}")
    lines.append(f"Завершённых заявок: {summary['total_completed']}")
    lines.append(f"Исключено из выплат: {summary['excluded_count']}")

    if summary["total_completed"]:
        avg = summary["total_gross"] / summary["total_completed"]
        lines.append(f"Средний чек: {_money(avg)}")

    lines.append("")
    lines.append(f"Общая выручка (gross): {_money(summary['total_gross'])}")
    lines.append(f"К выплате оператору: {_money(summary['total_share'])}")
    lines.append(f"  — выплачено: {_money(summary['paid_sum'])}")
    lines.append(f"  — pending:   {_money(summary['pending_sum'])}")

    # Per-order breakdown (last 15)
    if earnings:
        lines.append("\nПоследние завершённые заявки:")
        for e in earnings[:15]:
            status_icon = {"pending": "⏳", "paid": "✅", "excluded": "🚫", "adjusted": "✏️"}.get(
                e.status.value, "?"
            )
            order_n = f"№{e.order_id}"
            lines.append(
                f"  {status_icon} {order_n}: gross {_money(e.gross_amount)} "
                f"→ выплата {_money(e.operator_share)}"
                + (f" [{e.note}]" if e.note else "")
            )
        if len(earnings) > 15:
            lines.append(f"  ... и ещё {len(earnings) - 15} заявок")

    await message.answer("\n".join(lines))


# ── /payouts ──────────────────────────────────────────────────────────────────

@router.message(Command("payouts"), IsAdmin())
async def cmd_payouts(message: Message, session: AsyncSession):
    """
    /payouts           — all pending earnings grouped by operator
    /payouts @op|id    — pending earnings for one operator
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    earning_repo = EarningRepo(session)

    if not args:
        # All pending
        all_pending = await earning_repo.get_all_pending()
        if not all_pending:
            await message.answer("✅ Нет pending выплат")
            return

        # Group by operator
        by_op: dict[int, list] = {}
        for e in all_pending:
            by_op.setdefault(e.operator_id, []).append(e)

        lines = [f"⏳ Pending выплат: {len(all_pending)} заявок\n"]
        for op_id, items in by_op.items():
            op = items[0].operator
            total = sum(e.operator_share for e in items)
            mention = _mention(op)
            lines.append(f"👷 {mention} — {len(items)} заявок, итого: {_money(total)}")
            for e in items:
                lines.append(f"  №{e.order_id}: {_money(e.operator_share)}")
            lines.append(f"  → /markpaid {op.telegram_id}")
        await message.answer("\n".join(lines))
        return

    # Per-operator
    target = args[0]
    user_repo = UserRepo(session)
    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден")
        return

    pending = await earning_repo.get_by_operator(op.id, status=EarningStatus.pending)
    if not pending:
        await message.answer(f"✅ У {_mention(op)} нет pending выплат")
        return

    total = sum(e.operator_share for e in pending)
    lines = [f"⏳ Pending выплат для {_mention(op)}: {len(pending)} заявок\n"]
    for e in pending:
        lines.append(f"  №{e.order_id}: gross {_money(e.gross_amount)} → выплата {_money(e.operator_share)}")
    lines.append(f"\nИтого к выплате: {_money(total)}")
    lines.append(f"\n/markpaid {op.telegram_id} [примечание]")
    await message.answer("\n".join(lines))


# ── /markpaid ─────────────────────────────────────────────────────────────────

@router.message(Command("markpaid"), IsAdmin())
async def cmd_mark_paid(message: Message, session: AsyncSession):
    """
    /markpaid @op|id [note]  — mark all pending earnings for operator as paid
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    if not args:
        await message.answer(
            "Использование: /markpaid @username|telegram_id [примечание]\n"
            "Помечает все pending выплаты оператора как оплаченные"
        )
        return

    target = args[0]
    note = args[1].strip() if len(args) > 1 else None

    user_repo = UserRepo(session)
    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден")
        return

    admin = await user_repo.get_by_telegram_id(message.from_user.id)
    paid_by_id = admin.id if admin else None

    earning_repo = EarningRepo(session)
    paid = await earning_repo.mark_paid_all_pending(op.id, paid_by_id=paid_by_id, note=note)

    if not paid:
        await message.answer(f"ℹ️ У {_mention(op)} нет pending выплат")
        return

    total = sum(e.operator_share for e in paid)
    note_line = f"\nПримечание: {note}" if note else ""
    await message.answer(
        f"✅ Выплата для {_mention(op)} подтверждена\n"
        f"Заявок: {len(paid)}, сумма: {_money(total)}{note_line}"
    )


# ── /excludeearning ───────────────────────────────────────────────────────────

@router.message(Command("excludeearning"), IsAdmin())
async def cmd_exclude_earning(message: Message, session: AsyncSession):
    """
    /excludeearning {order_id} [reason]  — exclude order from payout
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    if not args or not args[0].isdigit():
        await message.answer("Использование: /excludeearning {order_id} [причина]")
        return

    order_id = int(args[0])
    reason = args[1].strip() if len(args) > 1 else None

    earning_repo = EarningRepo(session)
    earning = await earning_repo.get_by_order_id(order_id)
    if not earning:
        await message.answer(f"❌ Выплата по заявке №{order_id} не найдена (заявка не завершена или не имеет суммы)")
        return

    if earning.status == EarningStatus.excluded:
        await message.answer(f"ℹ️ Заявка №{order_id} уже исключена из выплат")
        return

    await earning_repo.exclude(earning, note=reason)
    reason_line = f"\nПричина: {reason}" if reason else ""
    await message.answer(
        f"🚫 Заявка №{order_id} исключена из выплат оператору{reason_line}"
    )


# ── /adjustearning ────────────────────────────────────────────────────────────

@router.message(Command("adjustearning"), IsAdmin())
async def cmd_adjust_earning(message: Message, session: AsyncSession):
    """
    /adjustearning {order_id} {amount} [reason]  — manually set operator payout amount
    """
    args = (message.text or "").split(maxsplit=3)[1:]
    if len(args) < 2 or not args[0].isdigit():
        await message.answer("Использование: /adjustearning {order_id} {сумма} [причина]")
        return

    order_id = int(args[0])
    try:
        new_amount = Decimal(args[1].replace(",", "."))
        if new_amount < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ Укажите корректную сумму (например: 1200 или 1200.50)")
        return

    reason = args[2].strip() if len(args) > 2 else None

    earning_repo = EarningRepo(session)
    earning = await earning_repo.get_by_order_id(order_id)
    if not earning:
        await message.answer(f"❌ Выплата по заявке №{order_id} не найдена")
        return

    old_amount = earning.operator_share
    await earning_repo.adjust(earning, new_share=new_amount, note=reason)
    reason_line = f"\nПричина: {reason}" if reason else ""
    await message.answer(
        f"✏️ Выплата по заявке №{order_id} скорректирована\n"
        f"Было: {_money(old_amount)} → Стало: {_money(new_amount)}{reason_line}"
    )
