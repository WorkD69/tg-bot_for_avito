from zoneinfo import ZoneInfo

from app.db.models.message import MessageDirection
from app.db.models.order import Order, OrderStatus

MSK = ZoneInfo("Europe/Moscow")


def _msk(dt) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def _money(amount) -> str:
    """Format Decimal amount as rubles without kopecks when not needed."""
    if amount is None:
        return "—"
    int_val = int(amount)
    return f"{int_val} ₽" if amount == int_val else f"{amount:.2f} ₽"


def _parse_comment_ts(part: str) -> tuple[str, str]:
    """Parse 'ISO_TS|text' comment entry. Falls back gracefully for legacy plain text."""
    if "|" in part:
        ts_raw, text = part.split("|", 1)
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts_raw).replace(tzinfo=timezone.utc).astimezone(MSK)
            return dt.strftime("%d.%m.%Y %H:%M"), text.strip()
        except ValueError:
            pass
    return "—", part.strip()


def _history_lines(order: Order) -> list[str]:
    """Build interaction history lines (comment + messages)."""
    lines = []
    if order.comment:
        for part in order.comment.split("\n---\n"):
            ts, text = _parse_comment_ts(part)
            lines.append(f"[{ts}] 👤 Клиент (комментарий): {text}")
    for msg in (order.messages or []):
        ts = msg.created_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
        icon = "👤 Клиент" if msg.direction == MessageDirection.client_to_op else "🔧 Оператор"
        lines.append(f"[{ts}] {icon}: {msg.text}")
    return lines


# ── Operator card (full info with bids) ──────────────────────────────────────

def format_order_card(order: Order) -> str:
    client = order.client
    client_name = f"@{client.username}" if client.username else client.full_name

    deadline = order.deadline.strftime("%d.%m.%Y") if order.deadline else "—"
    auction_end = _msk(order.auction_end_at)
    created = _msk(order.created_at)

    lines = [
        f"📌 Заявка №{order.id}",
        f"Статус: {order.status.value}",
        f"Клиент: {client_name}",
        f"Дата создания: {created} МСК",
        f"Дедлайн: {deadline}",
        f"Желаемый бюджет: {_money(order.budget)}",
        f"Сбор цен до: {auction_end} МСК",
    ]

    lines.append("Ставки операторов:")
    if order.bids:
        for bid in sorted(order.bids, key=lambda b: b.amount):
            op_name = (
                f"@{bid.operator.username}" if bid.operator.username else bid.operator.full_name
            )
            lines.append(f"  • {op_name}: {_money(bid.amount)}")
    else:
        lines.append("  Ставок пока нет")

    history = _history_lines(order)
    lines.append("История взаимодействия:")
    lines.extend(history) if history else lines.append("  Сообщений пока нет")

    return "\n".join(lines)


# ── Client active order card (no bids, no operator names) ────────────────────

def format_client_card(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m.%Y") if order.deadline else "—"
    created = _msk(order.created_at)

    lines = [
        f"📌 Заявка №{order.id}",
        f"Статус: {order.status.value}",
        f"Дата создания: {created} МСК",
        f"Дедлайн: {deadline}",
        f"Желаемый бюджет: {_money(order.budget)}",
    ]

    files_count = len(order.files) if order.files else 0
    lines.append(f"Прикреплённых файлов: {files_count}" if files_count else "Прикреплённых файлов: нет")

    history = _history_lines(order)
    lines.append("История взаимодействия:")
    lines.extend(history) if history else lines.append("  Сообщений пока нет")

    return "\n".join(lines)


# ── Client history card (cancelled / completed) ───────────────────────────────

def format_client_history_card(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m.%Y") if order.deadline else "—"
    created = _msk(order.created_at)
    updated = _msk(order.updated_at)

    status_date_label = (
        "Дата отмены" if order.status == OrderStatus.cancelled else "Дата выполнения"
    )

    lines = [
        f"📌 Заявка №{order.id}",
        f"Статус: {order.status.value}",
        f"Дата создания: {created} МСК",
        f"{status_date_label}: {updated} МСК",
        f"Дедлайн: {deadline}",
        f"Желаемый бюджет: {_money(order.budget)}",
    ]

    files_count = len(order.files) if order.files else 0
    lines.append(f"Прикреплённых файлов: {files_count}" if files_count else "Прикреплённых файлов: нет")

    history = _history_lines(order)
    lines.append("История взаимодействия:")
    lines.extend(history) if history else lines.append("  Сообщений пока нет")

    return "\n".join(lines)


def format_order_short(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m") if order.deadline else "—"
    return f"№{order.id} – {order.status.value} – {deadline}"
