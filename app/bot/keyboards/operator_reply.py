from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_FREE = "Свободные заявки"
BTN_MY = "Мои заявки"
BTN_DONE = "История выполненных заявок"


def operator_group_kb() -> ReplyKeyboardMarkup:
    """Keyboard for operator group chat (reply keyboard posted to the group)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FREE)],
            [KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_DONE)],
        ],
        resize_keyboard=True,
    )


def operator_dm_kb() -> ReplyKeyboardMarkup:
    """Keyboard for operator/admin in DM — operator sections + client-mode buttons.

    Operators can also create and manage orders as clients, so client buttons
    are included at the bottom rows. The top rows are operator-specific.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FREE), KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_DONE)],
            [KeyboardButton(text="Создать заявку"), KeyboardButton(text="Текущие заявки")],
            [KeyboardButton(text="История заявок"), KeyboardButton(text="Отзывы")],
        ],
        resize_keyboard=True,
    )
