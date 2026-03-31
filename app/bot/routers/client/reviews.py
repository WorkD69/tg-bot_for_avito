from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsClient
from app.bot.keyboards.admin_inline import rating_kb, reviews_menu_kb
from app.bot.keyboards.callbacks import OrderCB, RatingCB, ReviewListCB
from app.bot.keyboards.client_reply import BTN_REVIEWS, client_main_kb
from app.bot.states.note import ReviewStates
from app.config import settings
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo
from app.repositories.review_repo import ReviewRepo

router = Router()


@router.message(F.text == BTN_REVIEWS, IsClient())
async def reviews_menu(message: Message):
    await message.answer("⭐ Отзывы:", reply_markup=reviews_menu_kb())


@router.callback_query(ReviewListCB.filter(F.action == "all"))
async def all_reviews(callback: CallbackQuery, session: AsyncSession):
    reviews = await ReviewRepo(session).get_approved()
    if not reviews:
        await callback.message.answer("📭 Пока нет одобренных отзывов")
        await callback.answer()
        return

    lines = []
    for r in reviews:
        name = f"@{r.client.username}" if r.client.username else r.client.full_name
        stars = "⭐" * r.rating
        lines.append(f"{stars} {name}:\n{r.text}")
    await callback.message.answer("\n\n".join(lines))
    await callback.answer()


@router.callback_query(ReviewListCB.filter(F.action == "mine"), IsClient())
async def my_reviews(callback: CallbackQuery, session: AsyncSession, user: User):
    reviews = await ReviewRepo(session).get_by_client(user.id)
    if not reviews:
        await callback.message.answer("📭 У вас пока нет отзывов")
        await callback.answer()
        return

    lines = []
    for r in reviews:
        status = "✅ одобрен" if r.is_approved else "⏳ на модерации"
        stars = "⭐" * r.rating
        lines.append(f"{status} {stars}: {r.text}")
    await callback.message.answer("\n\n".join(lines))
    await callback.answer()


# ── Leave review FSM ──────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "review"), IsClient())
async def start_review(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.client_id != user.id:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    existing = await ReviewRepo(session).get_by_client(user.id)
    if any(r.order_id == order.id for r in existing):
        await callback.answer("ℹ️ Вы уже оставили отзыв по этой заявке", show_alert=True)
        return

    await state.set_state(ReviewStates.waiting_rating)
    await state.update_data(order_id=order.id)
    await callback.message.answer(
        "⭐ Оцените работу оператора:",
        reply_markup=rating_kb(order.id),
    )
    await callback.answer()


@router.callback_query(RatingCB.filter(), ReviewStates.waiting_rating, IsClient())
async def got_rating(
    callback: CallbackQuery,
    callback_data: RatingCB,
    state: FSMContext,
):
    await state.update_data(rating=callback_data.stars)
    await state.set_state(ReviewStates.waiting_text)
    stars = "⭐" * callback_data.stars
    await callback.message.answer(f"✅ Оценка {stars} выбрана\n\n✍️ Напишите ваш отзыв:")
    await callback.answer()


@router.message(ReviewStates.waiting_text, F.text, IsClient())
async def got_review_text(message: Message, state: FSMContext, session: AsyncSession, user: User):
    from aiogram.exceptions import TelegramBadRequest

    data = await state.get_data()
    order_id: int = data["order_id"]
    rating: int = data.get("rating", 5)
    review = await ReviewRepo(session).create(
        order_id=order_id, client_id=user.id, text=message.text.strip(), rating=rating,
    )
    await state.clear()

    from app.bot.keyboards.admin_inline import review_moderation_kb
    from app.bot.instance import bot

    client_name = f"@{user.username}" if user.username else user.full_name
    stars = "⭐" * rating
    admin_text = (
        f"📝 Новый отзыв от {client_name} по заявке №{order_id} ({stars}):\n\n{review.text}"
    )
    try:
        await bot.send_message(
            settings.admin_telegram_id,
            admin_text,
            reply_markup=review_moderation_kb(review.id),
        )
    except TelegramBadRequest:
        pass

    await message.answer(
        "🙏 Спасибо! Ваш отзыв отправлен на модерацию",
        reply_markup=client_main_kb(),
    )
