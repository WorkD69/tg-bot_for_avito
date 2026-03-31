from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.callbacks import OrderCB


def group_new_order_kb(order_id: int) -> InlineKeyboardMarkup:
    """Posted to operator group when a new order is created."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к заявке",
                    callback_data=OrderCB(order_id=order_id, action="view").pack(),
                )
            ]
        ]
    )


def free_order_card_kb(order_id: int) -> InlineKeyboardMarkup:
    """Operator DM — free order card (auction in progress)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Могу взять",
                    callback_data=OrderCB(order_id=order_id, action="bid").pack(),
                ),
                InlineKeyboardButton(
                    text="Файлы",
                    callback_data=OrderCB(order_id=order_id, action="files").pack(),
                ),
            ]
        ]
    )


def my_order_card_kb(order_id: int) -> InlineKeyboardMarkup:
    """Operator DM — assigned order card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Файлы",
                    callback_data=OrderCB(order_id=order_id, action="files").pack(),
                ),
                InlineKeyboardButton(
                    text="Написать клиенту",
                    callback_data=OrderCB(order_id=order_id, action="msg").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Добавить заметку",
                    callback_data=OrderCB(order_id=order_id, action="note").pack(),
                ),
                InlineKeyboardButton(
                    text="Отправить решение",
                    callback_data=OrderCB(order_id=order_id, action="solution").pack(),
                ),
            ],
        ]
    )


def files_view_kb(order_id: int) -> InlineKeyboardMarkup:
    """Replaces order card when 'Файлы' is pressed — shows back button."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=OrderCB(order_id=order_id, action="back").pack(),
                )
            ]
        ]
    )


def client_completed_order_kb(order_id: int) -> InlineKeyboardMarkup:
    """Client DM — completed order card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📂 Решение",
                    callback_data=OrderCB(order_id=order_id, action="solution").pack(),
                ),
                InlineKeyboardButton(
                    text="💬 Задать вопрос",
                    callback_data=OrderCB(order_id=order_id, action="msg").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Оставить отзыв",
                    callback_data=OrderCB(order_id=order_id, action="review").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=OrderCB(order_id=order_id, action="back_history").pack(),
                ),
            ],
        ]
    )


def client_cancelled_order_kb(order_id: int) -> InlineKeyboardMarkup:
    """Client DM — cancelled order card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=OrderCB(order_id=order_id, action="back_history").pack(),
                ),
            ]
        ]
    )


# Keep old name as alias for backward compat
def client_history_order_kb(order_id: int) -> InlineKeyboardMarkup:
    return client_completed_order_kb(order_id)


def client_active_order_kb(order_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    """Client DM — active order card (pending / awaiting_payment / in_progress)."""
    rows = [
        [
            InlineKeyboardButton(
                text="✏️ Добавить комментарий",
                callback_data=OrderCB(order_id=order_id, action="add_comment").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="📎 Добавить файлы",
                callback_data=OrderCB(order_id=order_id, action="add_files").pack(),
            ),
        ],
    ]
    if can_cancel:
        rows.append([
            InlineKeyboardButton(
                text="❌ Отменить заявку",
                callback_data=OrderCB(order_id=order_id, action="cancel").pack(),
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="← Назад",
            callback_data=OrderCB(order_id=order_id, action="back_list").pack(),
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """Cancel confirmation keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить",
                    callback_data=OrderCB(order_id=order_id, action="cancel_yes").pack(),
                ),
                InlineKeyboardButton(
                    text="← Нет, назад",
                    callback_data=OrderCB(order_id=order_id, action="cancel_no").pack(),
                ),
            ]
        ]
    )


def orders_list_kb(orders: list, *, finished: bool = False) -> InlineKeyboardMarkup:
    """Inline list of orders — each row is one order button."""
    rows = []
    for order in orders:
        deadline_str = order.deadline.strftime("%d.%m") if order.deadline else "—"
        label = f"№{order.id} – {order.status.value} – {deadline_str}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=OrderCB(order_id=order.id, action="view").pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_orders_list_kb(orders: list) -> InlineKeyboardMarkup:
    """Client's active orders list — uses client_view action to avoid operator handler conflict."""
    rows = []
    for order in orders:
        deadline_str = order.deadline.strftime("%d.%m") if order.deadline else "—"
        label = f"№{order.id} – {order.status.value} – {deadline_str}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=OrderCB(order_id=order.id, action="client_view").pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
