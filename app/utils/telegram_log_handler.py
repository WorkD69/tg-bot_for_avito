"""Custom logging handler that sends ERROR+ records to a Telegram chat.

Only fires when there is a running asyncio event loop (i.e. while the bot is
serving requests). Startup/shutdown errors won't be delivered but they will
still appear in docker-compose logs.
"""
import asyncio
import logging


class TelegramErrorHandler(logging.Handler):
    """Send ERROR and above to the given Telegram chat via HTTP."""

    def __init__(self, bot_token: str, chat_id: int) -> None:
        super().__init__(level=logging.ERROR)
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self.setFormatter(logging.Formatter(
            "%(name)s:%(lineno)d\n%(message)s",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._send(self.format(record)))
        except RuntimeError:
            pass  # No event loop running — skip silently

    async def _send(self, text: str) -> None:
        import aiohttp
        body = (
            f"⚠️ <b>Ошибка бота</b>\n\n"
            f"<pre>{text[:3800]}</pre>"
        )
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    self._url,
                    json={
                        "chat_id": self._chat_id,
                        "text": body,
                        "parse_mode": "HTML",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception:
            pass  # Never raise from inside a log handler
