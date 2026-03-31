from datetime import datetime, timezone

from aiogram import F, Router
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
    await state.update_data(order_id=order.id, files=[])
    await callback.message.answer(
        "📎 Отправьте файлы с решением — когда закончите, отправьте /done"
    )
    await callback.answer()


@router.message(SolutionStates.waiting_files, F.photo, IsOperator())
async def solution_photo(message: Message, state: FSMContext):
    await _add_solution_file(message, state, file_id=message.photo[-1].file_id, file_type="photo")


@router.message(SolutionStates.waiting_files, F.document, IsOperator())
async def solution_document(message: Message, state: FSMContext):
    await _add_solution_file(message, state, file_id=message.document.file_id, file_type="document")


async def _add_solution_file(message: Message, state: FSMContext, file_id: str, file_type: str):
    data = await state.get_data()
    files: list = data.get("files", [])
    files.append({"file_id": file_id, "file_type": file_type})
    await state.update_data(files=files)
    await message.answer(f"✅ Файл принят ({len(files)} шт.) — ещё или /done")


@router.message(SolutionStates.waiting_files, F.text == "/done", IsOperator())
async def solution_done(message: Message, state: FSMContext, session: AsyncSession, user: User):
    data = await state.get_data()
    files: list = data.get("files", [])
    if not files:
        await message.answer("❌ Нужен хотя бы один файл")
        return

    order_id: int = data["order_id"]
    await state.clear()

    order = await OrderRepo(session).get_by_id(order_id)
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
        ))

    # Mark solution uploaded — do NOT auto-complete the order
    order.solution_uploaded_at = datetime.now(timezone.utc)
    await session.flush()

    await OrderRepo(session).add_log(
        order_id=order_id,
        actor_id=user.id,
        action=OrderLogAction.solution_uploaded,
        detail=f"{len(files)} file(s)",
    )

    from app.bot.instance import bot
    from app.repositories.user_repo import UserRepo
    client = await UserRepo(session).get_by_id(order.client_id)
    if client:
        try:
            await bot.send_message(
                client.telegram_id,
                f"✅ Оператор загрузил решение по заявке №{order_id}\n"
                "📂 Посмотрите в разделе «История заявок»",
            )
        except Exception:
            pass

    await message.answer(f"✅ Решение по заявке №{order_id} отправлено клиенту")
