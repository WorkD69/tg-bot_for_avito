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
                    callback_data=ReviewListCB(action="all", page=0).pack(),
                ),
            ]
        ]
    )


def reviews_pagination_kb(page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    """Prev/Next navigation for reviews list. Returns None if only one page."""
    if total_pages <= 1:
        return None
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(
            text="← Назад",
            callback_data=ReviewListCB(action="all", page=page - 1).pack(),
        ))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(
            text="Далее →",
            callback_data=ReviewListCB(action="all", page=page + 1).pack(),
        ))
    return InlineKeyboardMarkup(inline_keyboard=[row])
