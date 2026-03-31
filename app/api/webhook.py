from aiogram.types import Update
from fastapi import APIRouter, Header, HTTPException, Request

from app.bot.instance import bot
from app.config import settings

router = APIRouter()


@router.post(settings.webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    from app.main import dp  # avoid circular import at module level

    body = await request.json()
    update = Update.model_validate(body)
    await dp.feed_update(bot=bot, update=update)
    return {"ok": True}
