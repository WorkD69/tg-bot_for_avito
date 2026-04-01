import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.db.engine import AsyncSessionFactory

logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """Opens a DB session per update, commits after handler, runs post-commit tasks last.

    post_commit: list
        Handlers can append coroutines to data["post_commit"].
        They are awaited AFTER session.commit() succeeds — safe for external notifications.
        If commit fails (rollback), post_commit tasks are NOT executed.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        post_commit: list = []
        data["post_commit"] = post_commit

        async with AsyncSessionFactory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        # Session is closed and committed — safe to fire external notifications
        for coro in post_commit:
            try:
                await coro
            except Exception:
                logger.exception("post-commit notification failed")

        return result
