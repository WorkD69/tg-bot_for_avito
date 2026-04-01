from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsAdmin
from app.bot.keyboards.callbacks import ReviewCB
from app.db.models.review import ReviewStatus
from app.repositories.review_repo import ReviewRepo

router = Router()


@router.callback_query(ReviewCB.filter(F.action == "approve"), IsAdmin())
async def approve_review(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    session: AsyncSession,
):
    review = await ReviewRepo(session).get_by_id(callback_data.review_id)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    if review.status != ReviewStatus.pending:
        await callback.answer("ℹ️ Отзыв уже обработан", show_alert=True)
        return

    from app.repositories.user_repo import UserRepo
    actor = await UserRepo(session).get_by_telegram_id(callback.from_user.id)

    await ReviewRepo(session).set_status(review, ReviewStatus.approved, moderator_id=actor.id if actor else None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✅ Отзыв #{review.id} одобрен и опубликован")
    await callback.answer()


@router.callback_query(ReviewCB.filter(F.action == "reject"), IsAdmin())
async def reject_review(
    callback: CallbackQuery,
    callback_data: ReviewCB,
    session: AsyncSession,
):
    review = await ReviewRepo(session).get_by_id(callback_data.review_id)
    if not review:
        await callback.answer("Отзыв не найден", show_alert=True)
        return
    if review.status != ReviewStatus.pending:
        await callback.answer("ℹ️ Отзыв уже обработан", show_alert=True)
        return

    from app.repositories.user_repo import UserRepo
    actor = await UserRepo(session).get_by_telegram_id(callback.from_user.id)

    # Soft delete — set status=rejected, keep in DB
    await ReviewRepo(session).set_status(review, ReviewStatus.rejected, moderator_id=actor.id if actor else None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ Отзыв #{review.id} отклонён")
    await callback.answer()
