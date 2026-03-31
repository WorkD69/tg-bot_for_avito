from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_FREE = "Свободные заявки"
BTN_MY = "Мои заявки"
BTN_DONE = "История выполненных заявок"


def operator_group_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_FREE)],
            [KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_DONE)],
        ],
        resize_keyboard=True,
    )
