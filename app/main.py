from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.dispatcher import create_dispatcher
from app.bot.instance import bot
from app.config import settings

dp = create_dispatcher()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    # Set Telegram webhook
    await bot.set_webhook(
        url=settings.webhook_full_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
    )

    # Start APScheduler (registered in scheduler/setup.py, пункт 11)
    from app.scheduler.setup import scheduler
    scheduler.start()

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
