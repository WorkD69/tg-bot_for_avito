from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.callbacks import NegotCB, OrderCB


def group_new_order_kb(order_id: int) -> InlineKeyboardMarkup:
    """Posted to operator group when a new order is created or updated."""
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
    """Operator DM — assigned order card (in_progress)."""
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


def awaiting_payment_operator_kb(order_id: int) -> InlineKeyboardMarkup:
    """Operator DM — awaiting_payment card."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Отправить реквизиты",
                    callback_data=OrderCB(order_id=order_id, action="send_req").pack(),
                ),
            ],
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
        ]
    )


def send_requisites_kb(order_id: int) -> InlineKeyboardMarkup:
    """Shortcut keyboard after operator assignment — one button to send requisites."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Отправить реквизиты",
                    callback_data=OrderCB(order_id=order_id, action="send_req").pack(),
                )
            ]
        ]
    )


def client_awaiting_payment_kb(order_id: int, show_paid_btn: bool = True) -> InlineKeyboardMarkup:
    """Client DM — awaiting_payment card.

    show_paid_btn: True in manual requisites mode (client confirms payment manually).
                   False in online Robokassa mode (payment is confirmed via webhook).
    """
    rows = []
    if show_paid_btn:
        rows.append([
            InlineKeyboardButton(
                text="✅ Я оплатил",
                callback_data=OrderCB(order_id=order_id, action="pay").pack(),
            ),
        ])
    rows.append([
        InlineKeyboardButton(
            text="💬 Обсудить цену",
            callback_data=OrderCB(order_id=order_id, action="negotiate").pack(),
        ),
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


def negot_operator_kb(order_id: int, proposed_amount: str = "") -> InlineKeyboardMarkup:
    """Operator response to client price negotiation.

    proposed_amount: decimal string of client's counter-offer (empty = general message, no price).
    Encoded into the Accept button so the accept handler knows which price to apply officially.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять предложение",
                    callback_data=NegotCB(
                        order_id=order_id, action="accept", proposed_amount=proposed_amount
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💬 Встречная сумма",
                    callback_data=NegotCB(order_id=order_id, action="counter").pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отменить заявку",
                    callback_data=NegotCB(order_id=order_id, action="cancel").pack(),
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
                    # client_solution — separate from operator's "solution" (upload) action
                    callback_data=OrderCB(order_id=order_id, action="client_solution").pack(),
                ),
                InlineKeyboardButton(
                    text="💬 Задать вопрос",
                    callback_data=OrderCB(order_id=order_id, action="client_msg").pack(),
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


# Legacy alias
def client_history_order_kb(order_id: int) -> InlineKeyboardMarkup:
    return client_completed_order_kb(order_id)


def client_active_order_kb(order_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    """Client DM — active order card (pending / in_progress)."""
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


def orders_list_kb(orders: list) -> InlineKeyboardMarkup:
    """Inline list of orders for operator — each row is one order button."""
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
    """Client's orders list — uses client_view action to avoid operator handler conflict."""
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
