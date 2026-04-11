from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models.user import KNOWN_SOURCES, UserRole
from app.repositories.user_repo import UserRepo


def _extract_source(event: "Update") -> str:
    """Extract attribution source from /start deeplink payload, if any."""
    if not event.message:
        return "unknown"
    text = event.message.text or ""
    # /start avito  →  payload = "avito"
    parts = text.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0] == "/start":
        payload = parts[1].strip().lower()
        if payload in KNOWN_SOURCES:
            return payload
        # non-empty but unknown payload → still "direct" (not a deeplink from external)
        return "direct"
    return "unknown"


class UserRegisterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        if session is None:
            return await handler(event, data)

        tg_user = None
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                tg_user = event.message.from_user
            elif event.callback_query and event.callback_query.from_user:
                tg_user = event.callback_query.from_user

        if tg_user is None:
            return await handler(event, data)

        repo = UserRepo(session)
        user = await repo.get_by_telegram_id(tg_user.id)

        is_admin = tg_user.id == settings.admin_telegram_id

        if user is None:
            full_name = tg_user.full_name or tg_user.first_name or "Unknown"
            source = _extract_source(event) if isinstance(event, Update) else "unknown"
            user = await repo.create(
                telegram_id=tg_user.id,
                full_name=full_name,
                username=tg_user.username,
                source=source,
            )
            if is_admin and user.role != UserRole.admin:
                await repo.set_role(user, UserRole.admin)
        else:
            # Keep name/username in sync
            changed = False
            full_name = tg_user.full_name or tg_user.first_name or user.full_name
            if user.full_name != full_name:
                user.full_name = full_name
                changed = True
            if user.username != tg_user.username:
                user.username = tg_user.username
                changed = True
            # Auto-promote admin if not yet promoted
            if is_admin and user.role != UserRole.admin:
                await repo.set_role(user, UserRole.admin)
                changed = True
            if changed:
                await session.flush()

        data["user"] = user
        return await handler(event, data)
