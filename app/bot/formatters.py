from decimal import Decimal
from zoneinfo import ZoneInfo

from app.db.models.message import MessageDirection
from app.db.models.order import Order, OrderStatus

MSK = ZoneInfo("Europe/Moscow")


def _msk(dt) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


def _money(amount) -> str:
    """Format Decimal amount as rubles without kopecks when not needed. No float."""
    if amount is None:
        return "—"
    d = Decimal(str(amount))
    int_val = int(d)
    return f"{int_val} ₽" if d == int_val else f"{d:.2f} ₽"


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
    """Build interaction history lines (comment + messages). Max 10 entries."""
    lines = []
    if order.comment:
        for part in order.comment.split("\n---\n"):
            ts, text = _parse_comment_ts(part)
            lines.append(f"[{ts}] 👤 Клиент (комментарий): {text}")
    for msg in (order.messages or []):
        ts = msg.created_at.astimezone(MSK).strftime("%d.%m.%Y %H:%M")
        icon = "👤 Клиент" if msg.direction == MessageDirection.client_to_operator else "🔧 Оператор"
        lines.append(f"[{ts}] {icon}: {msg.text}")
    # Limit history to last 10 entries to keep card readable
    return lines[-10:]


# ── Operator card (full info with bids + notes) ───────────────────────────────

def format_order_card(order: Order, is_admin: bool = False, viewer_id: int | None = None) -> str:
    """Format order card for operators/admins.

    viewer_id — DB user.id of the viewer. If provided and is_admin=False,
    competitor bid amounts are hidden: operator sees only own bid + count of others.
    """
    client = order.client
    if is_admin:
        client_name = f"@{client.username}" if client.username else client.full_name
    else:
        client_name = client.full_name

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
    ]

    if order.status == OrderStatus.pending:
        lines.append(f"Сбор цен до: {auction_end} МСК")

    if order.payment_amount and order.status != OrderStatus.pending:
        from app.services.payment_service import PaymentService
        pay = PaymentService.client_amount(order)
        if order.discount_percent:
            lines.append(
                f"Согласованная сумма: {_money(order.payment_amount)} "
                f"(клиент платит {_money(pay)} — промокод {order.applied_promo} {order.discount_percent:.0f}%)"
            )
        else:
            lines.append(f"Согласованная сумма: {_money(order.payment_amount)}")

    lines.append("Ставки операторов:")
    if order.bids:
        if is_admin:
            # Admin sees all bids with amounts and names
            for bid in sorted(order.bids, key=lambda b: b.amount):
                op_name = (
                    f"@{bid.operator.username}" if bid.operator.username else bid.operator.full_name
                )
                lines.append(f"  • {op_name}: {_money(bid.amount)}")
        else:
            # Operator sees only own bid + count of others (no competitor prices)
            own = [b for b in order.bids if viewer_id is not None and b.operator_id == viewer_id]
            other_count = len(order.bids) - len(own)
            if own:
                lines.append(f"  Ваша ставка: {_money(own[0].amount)}")
            if other_count > 0:
                lines.append(f"  Других ставок: {other_count}")
            if not own and not other_count:
                lines.append("  Ставок пока нет")
    else:
        lines.append("  Ставок пока нет")

    history = _history_lines(order)
    lines.append("История взаимодействия:")
    lines.extend(history) if history else lines.append("  Сообщений пока нет")

    # Show operator notes
    if order.notes:
        lines.append("📝 Заметки:")
        for note in order.notes[-3:]:  # last 3 notes
            ts = note.created_at.astimezone(MSK).strftime("%d.%m %H:%M")
            lines.append(f"  [{ts}] {note.text}")

    return "\n".join(lines)


# Status hint shown to client — explains what the current status means in plain language
_CLIENT_STATUS_HINT: dict[str, str] = {
    "pending": "👀 Операторы видят заявку и подбирают цену — уведомим как только будет предложение",
    "awaiting_payment": "💳 Оплатите, чтобы оператор приступил к работе",
    "in_progress": "🔧 Оператор работает над задачей",
}


# ── Client active order card (no bids, no operator names) ────────────────────

def format_client_card(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m.%Y") if order.deadline else "—"
    created = _msk(order.created_at)

    lines = [
        f"📌 Заявка №{order.id}  ·  {order.status.value}",
        f"",
        f"📅 Создана: {created}  ·  Дедлайн: {deadline}",
        f"💰 Желаемый бюджет: {_money(order.budget)}",
    ]

    if order.payment_amount and order.status == OrderStatus.awaiting_payment:
        from app.services.payment_service import PaymentService
        pay = PaymentService.client_amount(order)
        if order.discount_percent:
            lines.append(
                f"💳 Сумма к оплате: {_money(pay)} "
                f"(скидка {order.discount_percent:.0f}% по промокоду {order.applied_promo})"
            )
        else:
            lines.append(f"💳 Сумма к оплате: {_money(pay)}")

    files_count = len(order.files) if order.files else 0
    if files_count:
        lines.append(f"📎 Файлов прикреплено: {files_count}")

    hint = _CLIENT_STATUS_HINT.get(order.status.name)
    if hint:
        lines.append(f"")
        lines.append(hint)

    history = _history_lines(order)
    if history:
        lines.append("")
        lines.append("💬 История:")
        lines.extend(history)

    return "\n".join(lines)


# ── Client history card (cancelled / completed) ───────────────────────────────

def format_client_history_card(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m.%Y") if order.deadline else "—"
    created = _msk(order.created_at)
    updated = _msk(order.updated_at)

    status_date_label = (
        "🚫 Отменена" if order.status == OrderStatus.cancelled else "✅ Выполнена"
    )

    lines = [
        f"📌 Заявка №{order.id}  ·  {order.status.value}",
        f"",
        f"📅 Создана: {created}  ·  Дедлайн: {deadline}",
        f"{status_date_label}: {updated}",
        f"💰 Желаемый бюджет: {_money(order.budget)}",
    ]

    if order.payment_amount:
        from app.services.payment_service import PaymentService
        pay = PaymentService.client_amount(order)
        if order.discount_percent:
            lines.append(
                f"💳 Итоговая сумма: {_money(pay)} "
                f"(скидка {order.discount_percent:.0f}% по промокоду {order.applied_promo})"
            )
        else:
            lines.append(f"💳 Итоговая сумма: {_money(pay)}")

    files_count = len(order.files) if order.files else 0
    if files_count:
        lines.append(f"📎 Файлов прикреплено: {files_count}")

    history = _history_lines(order)
    if history:
        lines.append("")
        lines.append("💬 История:")
        lines.extend(history)

    return "\n".join(lines)


def format_order_short(order: Order) -> str:
    deadline = order.deadline.strftime("%d.%m") if order.deadline else "—"
    return f"№{order.id} – {order.status.value} – {deadline}"
