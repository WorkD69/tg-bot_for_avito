from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters import IsOperator
from app.bot.keyboards.callbacks import OrderCB
from app.bot.keyboards.order_inline import files_view_kb
from app.bot.states.note import FilesViewState
from app.db.models.user import User
from app.repositories.order_repo import OrderRepo

router = Router()


# ── "Файлы" → edit message to files view ─────────────────────────────────────

@router.callback_query(OrderCB.filter(F.action == "files"), IsOperator())
async def show_files(
    callback: CallbackQuery,
    callback_data: OrderCB,
    state: FSMContext,
    session: AsyncSession,
    user: User,
):
    from app.bot.states.note import SolutionStates
    current_state = await state.get_state()
    if current_state == SolutionStates.waiting_files:
        await callback.answer(
            "⚠️ Загрузка решения в процессе — сначала завершите /done",
            show_alert=True,
        )
        return

    order = await OrderRepo(session).get_by_id(callback_data.order_id, load_relations=True)
    if not order:
        await callback.answer("❌ Заявка не найдена", show_alert=True)
        return

    # Guard: cannot view files of another operator's assigned order via stale callback
    if order.operator_id is not None and order.operator_id != user.id:
        await callback.answer("🔒 Заявка назначена другому оператору", show_alert=True)
        return

    if not order.files:
        await callback.answer("📭 К этой заявке нет файлов", show_alert=True)
        return

    # Edit the card message to show files header + back button
    await callback.message.edit_text(
        f"📎 Файлы по заявке №{order.id}:",
        reply_markup=files_view_kb(order.id),
    )

    # Send actual files as regular messages (not replies) and track IDs for cleanup
    file_msg_ids: list[int] = []
    for f in order.files:
        if f.file_type == "photo":
            sent = await callback.message.answer_photo(f.telegram_file_id)
        else:
            sent = await callback.message.answer_document(f.telegram_file_id)
        file_msg_ids.append(sent.message_id)

    # Store IDs in FSM so "← Назад" can delete them
    await state.set_state(FilesViewState.viewing)
    await state.update_data(file_msg_ids=file_msg_ids, files_chat_id=callback.message.chat.id)

    await callback.answer()
