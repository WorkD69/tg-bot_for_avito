from aiogram import Router

from app.bot.routers.admin.commands import router as commands_router
from app.bot.routers.admin.reviews import router as reviews_router

router = Router()
router.include_router(commands_router)
router.include_router(reviews_router)
