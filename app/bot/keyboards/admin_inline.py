from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.callbacks import RatingCB, ReviewCB, ReviewListCB


def rating_kb(order_id: int) -> InlineKeyboardMarkup:
    """Numeric rating keyboard 1-5 (stars don't fit on one row)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{i + 1} ⭐",
                    callback_data=RatingCB(order_id=order_id, stars=i + 1).pack(),
                )
                for i in range(5)
            ]
        ]
    )


def review_moderation_kb(review_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=ReviewCB(review_id=review_id, action="approve").pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=ReviewCB(review_id=review_id, action="reject").pack(),
                ),
            ]
        ]
    )


def reviews_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отзывы о нас",
                    callback_data=ReviewListCB(action="all").pack(),
                ),
                InlineKeyboardButton(
                    text="Мои отзывы",
                    callback_data=ReviewListCB(action="mine").pack(),
                ),
            ]
        ]
    )
