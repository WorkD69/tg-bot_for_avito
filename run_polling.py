import asyncio
import logging

from app.bot.dispatcher import create_dispatcher
from app.bot.instance import bot
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def main() -> None:
    from app.utils.telegram_log_handler import TelegramErrorHandler
    tg_handler = TelegramErrorHandler(settings.bot_token, settings.admin_telegram_id)
    logging.getLogger().addHandler(tg_handler)

    dp = create_dispatcher()

    from app.scheduler.setup import scheduler
    scheduler.start()

    from app.services.auction_service import recover_overdue_auctions
    await recover_overdue_auctions(bot)

    from app.scheduler.jobs import remind_unconfirmed_payments
    scheduler.add_job(
        remind_unconfirmed_payments,
        trigger="cron",
        hour=10,
        minute=0,
        id="remind_unconfirmed_payments",
        replace_existing=True,
    )

    logging.getLogger(__name__).info("Starting polling...")
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
