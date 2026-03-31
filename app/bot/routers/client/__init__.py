from aiogram import Router

from app.bot.routers.client.menu import router as menu_router
from app.bot.routers.client.order_create import router as order_create_router
from app.bot.routers.client.order_list import router as order_list_router
from app.bot.routers.client.reviews import router as reviews_router
from app.bot.routers.client.messaging import router as messaging_router

router = Router()
router.include_router(menu_router)
router.include_router(order_create_router)
router.include_router(order_list_router)
router.include_router(reviews_router)
router.include_router(messaging_router)
