import logging
from datetime import datetime, timezone

from aiogram import F, Router

logger = logging.getLogger(__name__)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import OrderCB
from app.bot.states.note import NoteStates, SolutionStates
from app.db.models.operator_note import OperatorNote
from app.db.models.order import OrderStatus
from app.db.models.order_log import OrderLogAction
from app.db.models.solution_file import SolutionFile
from app.db.models.user import User
from app.db.models.message import MessageDirection
from app.repositories.message_repo import MessageRepo
from app.repositories.order_repo import OrderRepo

router = Router()


# ── Add note ──────────────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "note"), IsOperator())
async def start_note(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    current_state = await state.get_state()
    if current_state == SolutionStates.waiting_files:
        await callback.answer(
            "⚠️ Загрузка решения в процессе — сначала завершите /done",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return

    await state.set_state(NoteStates.waiting_note)
    await state.update_data(order_id=order.id)
    await callback.message.answer("📝 Введите заметку к заявке:")
    await callback.answer()


@router.message(NoteStates.waiting_note, F.text, IsOperator())
async def got_note(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    order_id: int = data["order_id"]
    await state.clear()

    # Re-validate ownership
    order = await OrderRepo(session).get_by_id(order_id)
    if not order or order.operator_id != user.id:
        await message.answer("❌ Заявка не найдена или не ваша")
        return

    session.add(OperatorNote(order_id=order_id, operator_id=user.id, text=message.text.strip()))
    await message.answer("✅ Заметка сохранена")


# ── Upload solution ───────────────────────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "solution"), IsOperator())
async def start_solution(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.in_progress:
        await callback.answer("⚠️ Можно загрузить решение только по заявке в работе", show_alert=True)
        return

    await state.set_state(SolutionStates.waiting_files)
    await state.update_data(order_id=order.id, files=[], comment="")
    await callback.message.answer(
        "📎 Отправьте файлы с решением\n"
        "💬 Можете также отправить текстовый комментарий к решению\n"
        "Когда закончите — отправьте /done"
    )
    await callback.answer()


@router.message(SolutionStates.waiting_files, F.photo, IsOperator())
async def solution_photo(message: Message, state: FSMContext):
    await _add_solution_file(
        message, state,
        file_id=message.photo[-1].file_id,
        file_type="photo",
        caption=message.caption,
    )


@router.message(SolutionStates.waiting_files, F.document, IsOperator())
async def solution_document(message: Message, state: FSMContext):
    await _add_solution_file(
        message, state,
        file_id=message.document.file_id,
        file_type="document",
        caption=message.caption,
    )


async def _add_solution_file(
    message: Message, state: FSMContext, file_id: str, file_type: str, caption: str | None = None
):
    data = await state.get_data()
    files: list = data.get("files", [])
    # Store caption alongside file so it can be saved to DB and shown to client
    files.append({"file_id": file_id, "file_type": file_type, "caption": caption or ""})
    await state.update_data(files=files)
    caption_hint = " (подпись сохранена)" if caption else ""
    await message.answer(f"✅ Файл принят ({len(files)} шт.){caption_hint} — ещё или /done")


@router.message(SolutionStates.waiting_files, F.text == "/done", IsOperator())
async def solution_done(message: Message, state: FSMContext, session: AsyncSession, user: User, post_commit: list):
    data = await state.get_data()
    files: list = data.get("files", [])
    comment: str = data.get("comment", "").strip()
    if not files:
        await message.answer("❌ Нужен хотя бы один файл")
        return

    order_id: int = data["order_id"]
    await state.clear()

    order = await OrderRepo(session).get_by_id_for_update(order_id)
    if not order or order.operator_id != user.id:
        await message.answer("❌ Заявка не найдена или не ваша")
        return
    if order.status != OrderStatus.in_progress:
        await message.answer("⚠️ Заявка больше не в работе")
        return

    for f in files:
        session.add(SolutionFile(
            order_id=order_id,
            telegram_file_id=f["file_id"],
            file_type=f["file_type"],
            caption=f.get("caption") or None,
        ))

    # Mark solution uploaded and auto-complete the order
    order.solution_uploaded_at = datetime.now(timezone.utc)
    await session.flush()

    order_repo = OrderRepo(session)
    await order_repo.add_log(
        order_id=order_id,
        actor_id=user.id,
        action=OrderLogAction.solution_uploaded,
        detail=f"{len(files)} file(s)",
    )
    await order_repo.update_status(order, OrderStatus.completed)
    await order_repo.add_log(
        order_id=order_id,
        actor_id=user.id,
        action=OrderLogAction.completed,
        detail="auto-completed after solution upload",
    )

    # Create earning record for payout tracking.
    # Admins completing orders receive 100% (they are the platform owner).
    # Regular operators receive the configured operator_payout_percent (70%).
    if order.payment_amount and order.operator_id:
        from app.config import settings as _settings
        from app.db.models.user import UserRole
        from app.repositories.earning_repo import EarningRepo
        payout_pct = (
            _settings.admin_payout_percent
            if user.role == UserRole.admin
            else _settings.operator_payout_percent
        )
        await EarningRepo(session).create_for_order(
            order_id=order_id,
            operator_id=order.operator_id,
            gross_amount=order.payment_amount,
            payout_percent=payout_pct,
        )

    # Save operator's comment as a message so it appears in client's history card
    if comment:
        await MessageRepo(session).create(
            order_id=order_id,
            sender_id=user.id,
            text=comment,
            direction=MessageDirection.operator_to_client,
        )

    from app.bot.instance import bot
    from app.repositories.user_repo import UserRepo
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        comment_line = f"\n\n💬 Комментарий оператора:\n{comment}" if comment else ""
        post_commit.append(bot.send_message(
            client.telegram_id,
            f"🎉 Заявка №{order_id} выполнена!{comment_line}\n\n"
            "📂 Посмотрите в разделе «История заявок»",
        ))

    # Schedule follow-up message to client 24h after completion
    from app.scheduler.jobs import schedule_followup_job
    schedule_followup_job(order_id)

    await message.answer(f"✅ Решение по заявке №{order_id} отправлено клиенту — заявка завершена")


@router.message(SolutionStates.waiting_files, F.text, ~F.text.startswith("/"), IsOperator())
async def solution_comment(message: Message, state: FSMContext):
    """Operator can send a text comment alongside solution files.
    Commands (starting with /) are excluded so /cancel falls through to the universal handler.
    """
    await state.update_data(comment=message.text.strip())
    await message.answer("✅ Комментарий сохранён — отправьте файлы или /done")


# ── Operator cannot complete order ───────────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "cant_do"), IsOperator())
async def cant_do_order(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    user: User,
    post_commit: list,
):
    order = await OrderRepo(session).get_by_id(callback_data.order_id)
    if not order or order.operator_id != user.id:
        await callback.answer("❌ Заявка не найдена или не ваша", show_alert=True)
        return
    if order.status != OrderStatus.in_progress:
        await callback.answer("⚠️ Это действие доступно только для заявки в работе", show_alert=True)
        return

    # Save note so the event is traceable
    session.add(OperatorNote(
        order_id=order.id,
        operator_id=user.id,
        text="Оператор сообщил: не могу выполнить заявку",
    ))

    from app.bot.instance import bot
    from app.config import settings
    from app.repositories.user_repo import UserRepo

    # Notify admin — deferred after commit
    post_commit.append(bot.send_message(
        settings.admin_telegram_id,
        f"⚠️ Оператор {user.full_name} сообщает, что не может выполнить заявку №{order.id}\n"
        f"Для отмены: /cancelorder {order.id}",
    ))

    # Notify client — deferred after commit
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        post_commit.append(bot.send_message(
            client.telegram_id,
            f"⏳ По заявке №{order.id} возникли трудности — администратор свяжется с вами",
        ))

    await callback.message.answer("✅ Администратор уведомлён")
    await callback.answer()
