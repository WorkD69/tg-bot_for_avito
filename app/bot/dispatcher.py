from aiogram import Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from app.config import settings


def create_dispatcher() -> Dispatcher:
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    from app.bot.middlewares.db_session import DbSessionMiddleware
    from app.bot.middlewares.user_register import UserRegisterMiddleware

    dp.update.middleware(DbSessionMiddleware())
    dp.update.middleware(UserRegisterMiddleware())

    # Error handler — must be included before domain routers
    from app.bot.routers.errors import router as errors_router
    dp.include_router(errors_router)

    # Routers: admin → operator → client (most specific role first)
    from app.bot.routers.admin import router as admin_router
    from app.bot.routers.operator import router as operator_router
    from app.bot.routers.client import router as client_router

    dp.include_router(admin_router)
    dp.include_router(operator_router)
    dp.include_router(client_router)

    return dp
