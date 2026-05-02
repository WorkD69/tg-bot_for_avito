"""Aiogram error handler.

Catches any unhandled exception that bubbles up from a message/callback
handler, formats a traceback, and sends it to the admin's DM.
"""
import logging
import traceback

from aiogram import Bot, Router
from aiogram.types import ErrorEvent

from app.config import settings

logger = logging.getLogger(__name__)
router = Router()


@router.errors()
async def handle_unhandled_error(event: ErrorEvent, bot: Bot) -> bool:
    """Forward unhandled handler exceptions to the admin as a Telegram message."""
    exc = event.exception
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_text = "".join(tb_lines)

    # Also log locally so it appears in docker-compose logs
    logger.exception("Unhandled exception in update %s", event.update.update_id)

    import html as _html
    short_tb = tb_text[-3000:]  # keep the tail — most relevant part
    text = (
        f"🚨 <b>Необработанная ошибка</b>\n\n"
        f"<pre>{_html.escape(short_tb)}</pre>"
    )

    try:
        from app.db.engine import AsyncSessionFactory
        from app.db.models.user import UserRole
        from app.repositories.user_repo import UserRepo
        async with AsyncSessionFactory() as _s:
            admins = await UserRepo(_s).get_by_role(UserRole.admin)
        for admin in admins:
            try:
                await bot.send_message(admin.telegram_id, text, parse_mode="HTML")
            except Exception:
                pass
    except Exception:
        # Fallback to single admin if DB is unavailable
        try:
            await bot.send_message(settings.admin_telegram_id, text, parse_mode="HTML")
        except Exception:
            pass

    # Inform the user so they don't think the bot is frozen
    update = event.update
    try:
        if update.message:
            await update.message.answer("⚠️ Произошла ошибка — попробуйте ещё раз или напишите /start")
        elif update.callback_query:
            await update.callback_query.answer("⚠️ Произошла ошибка — попробуйте ещё раз", show_alert=True)
    except Exception:
        pass  # Don't let the user-notify attempt mask the original error

    return True  # Mark error as handled so aiogram doesn't re-raise
