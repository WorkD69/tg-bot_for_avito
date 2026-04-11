from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.dispatcher import create_dispatcher
from app.bot.instance import bot
from app.config import settings

dp = create_dispatcher()


def _validate_config() -> None:
    """Warn early about obviously wrong or missing config values.

    Does NOT raise — the bot can still run locally with partial config.
    Warnings appear in logs and (after the log handler is registered) in admin DM.
    """
    import logging
    log = logging.getLogger(__name__)

    if not settings.bot_token or settings.bot_token == "":
        log.error("STARTUP: BOT_TOKEN is not set — bot will not work")

    if not settings.admin_telegram_id:
        log.error("STARTUP: ADMIN_TELEGRAM_ID is not set")

    if settings.webhook_base_url in ("", "https://yourdomain.com"):
        log.warning("STARTUP: WEBHOOK_BASE_URL looks like a placeholder (%s) — set to ngrok URL or real domain", settings.webhook_base_url)

    if settings.webhook_secret in ("", "random_secret_string", "change_me_to_random_string"):
        log.warning("STARTUP: WEBHOOK_SECRET is default/empty — use a random secret in production")

    if settings.robokassa_is_test:
        log.info("STARTUP: Robokassa running in TEST mode — payments are simulated")

    if not settings.robokassa_login:
        log.info("STARTUP: ROBOKASSA_LOGIN is empty — manual payment mode active ('Ya oplatil' button)")

    # Check BACKUP_CHAT_ID via os.environ (it's not in Settings — backup service reads it directly)
    import os
    if not os.environ.get("BACKUP_CHAT_ID"):
        log.warning("STARTUP: BACKUP_CHAT_ID is not set — daily DB backups will not be sent to Telegram")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    # Route ERROR+ log records to admin's Telegram DM
    import logging
    from app.utils.telegram_log_handler import TelegramErrorHandler
    tg_handler = TelegramErrorHandler(settings.bot_token, settings.admin_telegram_id)
    logging.getLogger().addHandler(tg_handler)

    _validate_config()

    # Set Telegram webhook
    await bot.set_webhook(
        url=settings.webhook_full_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
    )

    # Start APScheduler
    from app.scheduler.setup import scheduler
    scheduler.start()

    # Recover any auctions that expired while the bot was offline
    from app.services.auction_service import recover_overdue_auctions
    await recover_overdue_auctions(bot)

    # Daily reminder: unconfirmed payments (fires at 10:00 UTC every day)
    from app.scheduler.jobs import remind_unconfirmed_payments
    scheduler.add_job(
        remind_unconfirmed_payments,
        trigger="cron",
        hour=10,
        minute=0,
        id="remind_unconfirmed_payments",
        replace_existing=True,
    )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    scheduler.shutdown(wait=False)
    await bot.delete_webhook()
    await bot.session.close()


def create_app() -> FastAPI:
    app = FastAPI(title="TG Bot for Avito", lifespan=lifespan)

    # API routers
    from app.api import webhook
    app.include_router(webhook.router)

    from app.api import payment
    app.include_router(payment.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
