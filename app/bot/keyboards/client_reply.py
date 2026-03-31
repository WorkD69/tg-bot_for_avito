from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_CREATE = "Создать заявку"
BTN_CURRENT = "Текущие заявки"
BTN_HISTORY = "История заявок"
BTN_REVIEWS = "Отзывы"


def client_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_CREATE), KeyboardButton(text=BTN_CURRENT)],
            [KeyboardButton(text=BTN_HISTORY), KeyboardButton(text=BTN_REVIEWS)],
        ],
        resize_keyboard=True,
    )
