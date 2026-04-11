"""Admin payout management commands.

Commands:
  /operatorstats [@op|id] [7d|30d]        — operator stats + earnings summary
  /payouts [@op|id]                        — payable earnings (pending + adjusted)
  /markpaid @op|id [note]                  — mark all payable earnings as paid
  /disputes                                — list all on_hold earnings requiring review
  /freezeearning {order_id} [reason]       — freeze earning (move to on_hold)
  /unfreezeearning {order_id} [note]       — unfreeze earning (move back to pending)
  /excludeearning {order_id} [reason]      — permanently exclude from payout
  /adjustearning {order_id} {amount} [reason] — manually override payout amount

Safety rules enforced here:
  - /payouts and /markpaid never touch on_hold earnings
  - /markpaid shows a warning if operator still has on_hold earnings after payment
  - /markpaid is blocked entirely if ALL payable earnings are zero and only on_hold exist
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

STATUS_ICON = {
    "pending": "⏳",
    "on_hold": "🔒",
    "adjusted": "✏️",
    "paid": "✅",
    "excluded": "🚫",
}


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
    /operatorstats @op          — detailed stats for one operator (all time)
    /operatorstats @op 7d       — last 7 days
    /operatorstats @op 30d      — last 30 days
    """
    args = (message.text or "").split(maxsplit=3)[1:]

    user_repo = UserRepo(session)
    earning_repo = EarningRepo(session)
    order_repo = OrderRepo(session)

    if not args:
        op_list = await user_repo.get_by_role(UserRole.operator)
        if not op_list:
            await message.answer("📭 Операторов пока нет")
            return

        lines = ["📊 Статистика операторов:\n"]
        for op in op_list:
            summary = await earning_repo.get_summary_by_operator(op.id)
            active = await order_repo.get_operator_active_orders(op.id)
            mention = _mention(op)
            hold_warn = f" ⚠️ {summary['on_hold_count']} заморожено" if summary["on_hold_count"] else ""
            if summary["total_completed"] == 0:
                lines.append(
                    f"👷 {mention}\n"
                    f"  Активных: {len(active)} | Завершённых: 0{hold_warn}\n"
                )
            else:
                lines.append(
                    f"👷 {mention}\n"
                    f"  Активных: {len(active)} | Завершённых: {summary['total_completed']}{hold_warn}\n"
                    f"  Gross: {_money(summary['total_gross'])} | "
                    f"Pending: {_money(summary['payable_sum'])} | "
                    f"Выплачено: {_money(summary['paid_sum'])}\n"
                )
        await message.answer("\n".join(lines))
        return

    target = args[0]
    period_token = args[1] if len(args) > 1 else None
    since = _parse_period(period_token)

    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден — он должен сначала написать боту")
        return

    summary = await earning_repo.get_summary_by_operator(op.id, since=since)
    active = await order_repo.get_operator_active_orders(op.id)
    earnings = await earning_repo.get_by_operator(op.id, since=since)

    period_label = f" (за {period_token})" if period_token else " (всё время)"
    mention = _mention(op)

    lines = [f"📊 Статистика {mention}{period_label}\n"]
    lines.append(f"Активных заявок: {len(active)}")
    lines.append(f"Завершённых заявок: {summary['total_completed']}")

    if summary["on_hold_count"]:
        lines.append(f"🔒 Заморожено (требует решения): {summary['on_hold_count']} на {_money(summary['on_hold_sum'])}")
    if summary["excluded_count"]:
        lines.append(f"🚫 Исключено из выплат: {summary['excluded_count']}")
    if summary["adjusted_count"]:
        lines.append(f"✏️ Скорректировано: {summary['adjusted_count']}")

    if summary["total_completed"]:
        avg = summary["total_gross"] / summary["total_completed"]
        lines.append(f"Средний чек: {_money(avg)}")

    lines.append("")
    lines.append(f"Общая выручка (gross): {_money(summary['total_gross'])}")
    lines.append(f"К выплате (payable): {_money(summary['payable_sum'])}")
    lines.append(f"  — выплачено: {_money(summary['paid_sum'])}")

    if earnings:
        lines.append("\nПоследние заявки:")
        for e in earnings[:15]:
            icon = STATUS_ICON.get(e.status.value, "?")
            lines.append(
                f"  {icon} №{e.order_id}: gross {_money(e.gross_amount)} "
                f"→ {_money(e.operator_share)}"
                + (f"  [{e.note}]" if e.note else "")
            )
        if len(earnings) > 15:
            lines.append(f"  ... и ещё {len(earnings) - 15}")

    await message.answer("\n".join(lines))


# ── /payouts ──────────────────────────────────────────────────────────────────

@router.message(Command("payouts"), IsAdmin())
async def cmd_payouts(message: Message, session: AsyncSession):
    """
    /payouts           — all payable earnings (pending + adjusted) grouped by operator
    /payouts @op|id    — payable earnings for one operator

    Shows adjusted earnings with ✏️ marker.
    Shows a warning if operator has on_hold earnings that are NOT included here.
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    earning_repo = EarningRepo(session)

    if not args:
        all_payable = await earning_repo.get_all_payable()
        all_on_hold = await earning_repo.get_on_hold()

        if not all_payable and not all_on_hold:
            await message.answer("✅ Нет выплат для обработки")
            return

        lines = []

        if all_payable:
            by_op: dict[int, list] = {}
            for e in all_payable:
                by_op.setdefault(e.operator_id, []).append(e)

            lines.append(f"⏳ К выплате: {len(all_payable)} заявок\n")
            for op_id, items in by_op.items():
                op = items[0].operator
                total = sum(e.operator_share for e in items)
                mention = _mention(op)
                lines.append(f"👷 {mention} — {len(items)} заявок, итого: {_money(total)}")
                for e in items:
                    icon = STATUS_ICON.get(e.status.value, "⏳")
                    lines.append(f"  {icon} №{e.order_id}: {_money(e.operator_share)}")
                lines.append(f"  → /markpaid {op.telegram_id}")
        else:
            lines.append("✅ Нет обычных pending выплат")

        if all_on_hold:
            by_op_hold: dict[int, list] = {}
            for e in all_on_hold:
                by_op_hold.setdefault(e.operator_id, []).append(e)
            lines.append(f"\n🔒 Заморожено (не включено в выплаты): {len(all_on_hold)} заявок")
            for op_id, items in by_op_hold.items():
                op = items[0].operator
                total = sum(e.operator_share for e in items)
                lines.append(f"  {_mention(op)}: {len(items)} заявок на {_money(total)}")
            lines.append("  Используйте /disputes для деталей")

        await message.answer("\n".join(lines))
        return

    # Per-operator
    target = args[0]
    user_repo = UserRepo(session)
    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден")
        return

    payable = await earning_repo.get_payable(op.id)
    on_hold = await earning_repo.get_on_hold(op.id)

    if not payable and not on_hold:
        await message.answer(f"✅ У {_mention(op)} нет выплат для обработки")
        return

    lines = []
    if payable:
        total = sum(e.operator_share for e in payable)
        lines.append(f"⏳ К выплате для {_mention(op)}: {len(payable)} заявок\n")
        for e in payable:
            icon = STATUS_ICON.get(e.status.value, "⏳")
            lines.append(f"  {icon} №{e.order_id}: gross {_money(e.gross_amount)} → {_money(e.operator_share)}")
        lines.append(f"\nИтого к выплате: {_money(total)}")
        lines.append(f"\n/markpaid {op.telegram_id} [примечание]")
    else:
        lines.append(f"✅ У {_mention(op)} нет payable выплат")

    if on_hold:
        hold_sum = sum(e.operator_share for e in on_hold)
        lines.append(f"\n🔒 Заморожено (НЕ включено): {len(on_hold)} заявок на {_money(hold_sum)}")
        for e in on_hold:
            note_txt = f" [{e.note}]" if e.note else ""
            lines.append(f"  №{e.order_id}: {_money(e.operator_share)}{note_txt}")
        lines.append("  Разморозить: /unfreezeearning {order_id}")
        lines.append("  Исключить: /excludeearning {order_id} причина")

    await message.answer("\n".join(lines))


# ── /markpaid ─────────────────────────────────────────────────────────────────

@router.message(Command("markpaid"), IsAdmin())
async def cmd_mark_paid(message: Message, session: AsyncSession):
    """
    /markpaid @op|id [note]

    Marks all PAYABLE (pending + adjusted) earnings for operator as paid.
    on_hold earnings are NEVER touched.
    Shows a warning if on_hold earnings remain after payment.
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    if not args:
        await message.answer(
            "Использование: /markpaid @username|telegram_id [примечание]\n"
            "Помечает все payable (pending + скорректированные) выплаты как оплаченные\n"
            "⚠️ Замороженные (on_hold) выплаты НЕ затрагиваются"
        )
        return

    target = args[0]
    note = args[1].strip() if len(args) > 1 else None

    user_repo = UserRepo(session)
    op = await _resolve_operator(target, user_repo)
    if not op:
        await message.answer("❌ Пользователь не найден")
        return

    earning_repo = EarningRepo(session)

    # Check on_hold first — inform admin
    on_hold = await earning_repo.get_on_hold(op.id)

    admin = await user_repo.get_by_telegram_id(message.from_user.id)
    paid_by_id = admin.id if admin else None

    paid = await earning_repo.mark_paid_all_payable(op.id, paid_by_id=paid_by_id, note=note)

    lines = []
    if not paid:
        lines.append(f"ℹ️ У {_mention(op)} нет payable выплат")
    else:
        total = sum(e.operator_share for e in paid)
        note_line = f"\nПримечание: {note}" if note else ""
        lines.append(
            f"✅ Выплата для {_mention(op)} подтверждена\n"
            f"Заявок: {len(paid)}, сумма: {_money(total)}{note_line}"
        )

    if on_hold:
        hold_sum = sum(e.operator_share for e in on_hold)
        lines.append(
            f"\n⚠️ Внимание: у {_mention(op)} {len(on_hold)} замороженных заявок "
            f"на {_money(hold_sum)} — они НЕ были выплачены\n"
            f"Разрешите споры: /disputes"
        )

    await message.answer("\n".join(lines))


# ── /disputes ─────────────────────────────────────────────────────────────────

@router.message(Command("disputes"), IsAdmin())
async def cmd_disputes(message: Message, session: AsyncSession):
    """
    /disputes   — list all on_hold earnings requiring admin review

    Shows who froze it, when, and why. Provides quick-action commands.
    """
    earning_repo = EarningRepo(session)
    on_hold = await earning_repo.get_on_hold()

    if not on_hold:
        await message.answer("✅ Нет замороженных выплат — всё чисто")
        return

    total_sum = sum(e.operator_share for e in on_hold)
    lines = [f"🔒 Замороженные выплаты: {len(on_hold)} заявок на {_money(total_sum)}\n"]
    lines.append("Требуют ручного решения:\n")

    from app.bot.formatters import _msk
    for e in on_hold:
        mention = _mention(e.operator)
        frozen_ts = _msk(e.frozen_at) if e.frozen_at else "—"
        note_txt = f"\n    Причина: {e.note}" if e.note else ""
        lines.append(
            f"🔒 №{e.order_id} | {mention}\n"
            f"    Gross: {_money(e.gross_amount)} → Share: {_money(e.operator_share)}\n"
            f"    Заморожено: {frozen_ts} МСК{note_txt}\n"
            f"    → /unfreezeearning {e.order_id}   или   /excludeearning {e.order_id} причина\n"
        )

    await message.answer("\n".join(lines))


# ── /freezeearning ────────────────────────────────────────────────────────────

@router.message(Command("freezeearning"), IsAdmin())
async def cmd_freeze_earning(message: Message, session: AsyncSession):
    """
    /freezeearning {order_id} [reason]

    Freezes an earning — moves it to on_hold. Prevents auto-payout.
    Use when a dispute is opened or something requires manual review.
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    if not args or not args[0].isdigit():
        await message.answer("Использование: /freezeearning {order_id} [причина]")
        return

    order_id = int(args[0])
    reason = args[1].strip() if len(args) > 1 else None

    user_repo = UserRepo(session)
    admin = await user_repo.get_by_telegram_id(message.from_user.id)
    frozen_by_id = admin.id if admin else None

    earning_repo = EarningRepo(session)
    earning = await earning_repo.get_by_order_id(order_id)
    if not earning:
        await message.answer(
            f"❌ Выплата по заявке №{order_id} не найдена\n"
            "(заявка ещё не завершена или не имеет payment_amount)"
        )
        return

    if earning.status == EarningStatus.on_hold:
        await message.answer(f"ℹ️ Заявка №{order_id} уже заморожена")
        return

    if earning.status in (EarningStatus.paid, EarningStatus.excluded):
        await message.answer(
            f"⚠️ Нельзя заморозить: заявка №{order_id} уже в статусе {earning.status.value}"
        )
        return

    await earning_repo.freeze(earning, frozen_by_id=frozen_by_id, note=reason)
    reason_line = f"\nПричина: {reason}" if reason else ""
    await message.answer(
        f"🔒 Выплата по заявке №{order_id} заморожена{reason_line}\n"
        f"Разморозить: /unfreezeearning {order_id}\n"
        f"Исключить навсегда: /excludeearning {order_id} причина"
    )


# ── /unfreezeearning ──────────────────────────────────────────────────────────

@router.message(Command("unfreezeearning"), IsAdmin())
async def cmd_unfreeze_earning(message: Message, session: AsyncSession):
    """
    /unfreezeearning {order_id} [note]

    Unfreezes an on_hold earning — moves it back to pending.
    Use when dispute is resolved in favor of the operator.
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    if not args or not args[0].isdigit():
        await message.answer("Использование: /unfreezeearning {order_id} [примечание]")
        return

    order_id = int(args[0])
    note = args[1].strip() if len(args) > 1 else None

    earning_repo = EarningRepo(session)
    earning = await earning_repo.get_by_order_id(order_id)
    if not earning:
        await message.answer(f"❌ Выплата по заявке №{order_id} не найдена")
        return

    if earning.status != EarningStatus.on_hold:
        await message.answer(
            f"ℹ️ Заявка №{order_id} не заморожена (статус: {earning.status.value})"
        )
        return

    await earning_repo.unfreeze(earning, note=note)
    note_line = f"\nПримечание: {note}" if note else ""
    await message.answer(
        f"✅ Выплата по заявке №{order_id} разморожена — теперь в статусе pending{note_line}\n"
        f"Включена в следующую выплату: /payouts"
    )


# ── /excludeearning ───────────────────────────────────────────────────────────

@router.message(Command("excludeearning"), IsAdmin())
async def cmd_exclude_earning(message: Message, session: AsyncSession):
    """
    /excludeearning {order_id} [reason]  — permanently exclude order from payout
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
        await message.answer(
            f"❌ Выплата по заявке №{order_id} не найдена "
            "(заявка не завершена или не имеет суммы)"
        )
        return

    if earning.status == EarningStatus.excluded:
        await message.answer(f"ℹ️ Заявка №{order_id} уже исключена из выплат")
        return

    if earning.status == EarningStatus.paid:
        await message.answer(f"⚠️ Нельзя исключить: заявка №{order_id} уже выплачена")
        return

    await earning_repo.exclude(earning, note=reason)
    reason_line = f"\nПричина: {reason}" if reason else ""
    await message.answer(
        f"🚫 Заявка №{order_id} исключена из выплат оператору навсегда{reason_line}"
    )


# ── /adjustearning ────────────────────────────────────────────────────────────

@router.message(Command("adjustearning"), IsAdmin())
async def cmd_adjust_earning(message: Message, session: AsyncSession):
    """
    /adjustearning {order_id} {amount} [reason]  — manually set operator payout amount

    Note: if earning is on_hold, it stays on_hold after adjustment.
    Unfreeze separately to make it payable.
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

    if earning.status == EarningStatus.paid:
        await message.answer(f"⚠️ Нельзя изменить: заявка №{order_id} уже выплачена")
        return

    old_amount = earning.operator_share
    was_on_hold = earning.status == EarningStatus.on_hold
    await earning_repo.adjust(earning, new_share=new_amount, note=reason)
    reason_line = f"\nПричина: {reason}" if reason else ""
    hold_note = "\n⚠️ Заявка осталась замороженной — разморозьте отдельно: /unfreezeearning {order_id}" if was_on_hold else ""
    await message.answer(
        f"✏️ Выплата по заявке №{order_id} скорректирована\n"
        f"Было: {_money(old_amount)} → Стало: {_money(new_amount)}{reason_line}{hold_note}"
    )
