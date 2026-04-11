"""Admin analytics commands.

Commands:
  /stats                          — full business snapshot (orders + payout + funnel top)
  /funnelstats [period]           — funnel with conversion rates per stage
  /sourcestats [period]           — user acquisition breakdown by source

Period tokens: today | 7d | 30d | all (default: all time)
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.formatters import _money
from app.repositories.analytics_repo import AnalyticsRepo

router = Router()

SOURCE_LABEL = {
    "avito": "Авито",
    "tg_channel": "Telegram-канал",
    "direct": "Прямой (deeplink)",
    "unknown": "Неизвестно",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_period(token: str | None) -> tuple[datetime | None, str]:
    """Return (since_datetime, label_string)."""
    if not token:
        return None, "всё время"
    t = token.strip().lower()
    if t in ("all", "all_time", "alltime"):
        return None, "всё время"
    if t == "today":
        since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return since, "сегодня"
    if t == "7d":
        return datetime.now(timezone.utc) - timedelta(days=7), "7 дней"
    if t == "30d":
        return datetime.now(timezone.utc) - timedelta(days=30), "30 дней"
    return None, "всё время"


def _conv(a: int, b: int) -> str:
    """Conversion rate from stage a to stage b as percentage string."""
    if not a:
        return "—"
    return f"{b / a * 100:.1f}%"


def _pct_bar(pct: float, width: int = 10) -> str:
    """Simple ASCII bar for source breakdown."""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


# ── /stats ────────────────────────────────────────────────────────────────────

@router.message(Command("stats"), IsAdmin())
async def cmd_stats(message: Message, session: AsyncSession):
    """Full business snapshot: orders by status + funnel + payout health."""
    repo = AnalyticsRepo(session)
    summary = await repo.get_business_summary()
    funnel = await repo.get_funnel()

    # Order status section
    by_status = summary["orders_by_status"]
    total_orders = sum(by_status.values())

    status_lines = []
    order_status_icons = {
        "На рассмотрении": "🔍",
        "Ожидает оплаты": "💳",
        "В работе": "⚙️",
        "Выполнено": "✅",
        "Отменено": "❌",
    }
    for status_val, cnt in by_status.items():
        icon = order_status_icons.get(status_val, "•")
        status_lines.append(f"  {icon} {status_val}: {cnt}")

    lines = ["📊 Бизнес-сводка\n"]
    lines.append(f"Всего заявок: {total_orders}")
    if status_lines:
        lines.extend(status_lines)

    # Funnel highlights
    lines.append("")
    lines.append(f"👤 Пользователей: {funnel['users_registered']}")
    lines.append(f"📋 Заявок создано: {funnel['orders_created']}")
    if funnel["orders_created"]:
        completed_rate = _conv(funnel["orders_created"], funnel["orders_completed"])
        cancel_rate = _conv(funnel["orders_created"], funnel["orders_cancelled"])
        lines.append(f"✅ Завершено: {funnel['orders_completed']} ({completed_rate} от созданных)")
        lines.append(f"❌ Отменено: {funnel['orders_cancelled']} ({cancel_rate} от созданных)")
    if funnel["orders_disputed"]:
        lines.append(f"⚖️ Споров: {funnel['orders_disputed']}")
    if funnel["reviews_left"]:
        lines.append(f"⭐ Отзывов: {funnel['reviews_left']}")

    # Payout section
    lines.append("")
    lines.append(f"💰 Выручка (gross): {_money(summary['total_gross'])}")
    lines.append(f"⏳ К выплате операторам: {_money(summary['payable_sum'])}")
    lines.append(f"✅ Выплачено: {_money(summary['paid_sum'])}")
    if summary["on_hold_count"]:
        lines.append(
            f"🔒 Заморожено: {summary['on_hold_count']} заявок на {_money(summary['on_hold_sum'])} "
            f"— используйте /disputes"
        )

    # Cancellations breakdown
    if summary["cancellations"]:
        lines.append("")
        cancel_by = {"client": "клиент", "operator": "оператор", "admin": "администратор", "system": "система"}
        parts = [f"{cancel_by.get(k, k)}: {v}" for k, v in summary["cancellations"].items()]
        lines.append(f"❌ Отмены по инициатору: {', '.join(parts)}")

    await message.answer("\n".join(lines))


# ── /funnelstats ──────────────────────────────────────────────────────────────

@router.message(Command("funnelstats"), IsAdmin())
async def cmd_funnel_stats(message: Message, session: AsyncSession):
    """
    /funnelstats [period]

    Full funnel with absolute counts + conversion rates between stages.
    Period: today | 7d | 30d | all (default: all time)
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    since, period_label = _parse_period(args[0] if args else None)

    repo = AnalyticsRepo(session)
    f = await repo.get_funnel(since=since)

    lines = [f"📈 Воронка ({period_label})\n"]

    # Stage definitions: (label, value, previous_value_for_conv)
    stages = [
        ("👤 Зарегистрировались", f["users_registered"], None),
        ("📋 Создали заявку", f["orders_created"], f["users_registered"]),
        ("🤝 Получили ставку", f["orders_got_bid"], f["orders_created"]),
        ("👷 Назначен оператор", f["orders_assigned"], f["orders_got_bid"]),
        ("💳 Оплата подтверждена", f["orders_pay_confirmed"], f["orders_assigned"]),
        ("✅ Завершено", f["orders_completed"], f["orders_pay_confirmed"]),
    ]

    for label, count, prev in stages:
        conv_str = f"  ↳ {_conv(prev, count)} от пред." if prev is not None else ""
        lines.append(f"{label}: {count}{conv_str}")

    # Separate loss/exit metrics
    lines.append("")
    lines.append("— Потери / выходы —")
    lines.append(f"❌ Отменено заявок: {f['orders_cancelled']}")
    if f["orders_created"]:
        lines.append(f"   ({_conv(f['orders_created'], f['orders_cancelled'])} от созданных)")
    lines.append(f"⚖️ Открыто споров: {f['orders_disputed']}")
    lines.append(f"⭐ Оставлено отзывов: {f['reviews_left']}")
    if f["orders_completed"]:
        lines.append(f"   ({_conv(f['orders_completed'], f['reviews_left'])} от завершённых)")

    # Overall completion rate
    lines.append("")
    overall = _conv(f["orders_created"], f["orders_completed"])
    lines.append(f"🎯 Completion rate (создана → завершена): {overall}")

    await message.answer("\n".join(lines))


# ── /sourcestats ──────────────────────────────────────────────────────────────

@router.message(Command("sourcestats"), IsAdmin())
async def cmd_source_stats(message: Message, session: AsyncSession):
    """
    /sourcestats [period]

    User acquisition breakdown by source (deeplink attribution).
    Period: today | 7d | 30d | all (default: all time)

    Sources are set at registration from /start {payload}:
      /start avito       → source = 'avito'
      /start tg_channel  → source = 'tg_channel'
      /start anything    → source = 'direct'
      (no payload)       → source = 'unknown'
    """
    args = (message.text or "").split(maxsplit=2)[1:]
    since, period_label = _parse_period(args[0] if args else None)

    repo = AnalyticsRepo(session)
    rows = await repo.get_source_breakdown(since=since)

    if not rows:
        await message.answer(f"📭 Нет данных о пользователях ({period_label})")
        return

    total = sum(r["count"] for r in rows)
    lines = [f"🔗 Источники пользователей ({period_label}) — всего: {total}\n"]

    for r in rows:
        label = SOURCE_LABEL.get(r["source"], r["source"])
        bar = _pct_bar(r["pct"])
        lines.append(f"{bar} {label}: {r['count']} ({r['pct']}%)")

    lines.append("")
    lines.append("Как настроить атрибуцию:")
    lines.append("  Deeplink: t.me/YourBot?start=avito")
    lines.append("  Доступные источники: avito, tg_channel, direct")

    await message.answer("\n".join(lines))
