from aiogram import Router

from app.bot.routers.operator.menu import router as menu_router
from app.bot.routers.operator.order_list import router as order_list_router
from app.bot.routers.operator.bid import router as bid_router
from app.bot.routers.operator.messaging import router as messaging_router
from app.bot.routers.operator.notes import router as notes_router

router = Router()
router.include_router(menu_router)
router.include_router(order_list_router)
router.include_router(bid_router)
router.include_router(messaging_router)
router.include_router(notes_router)
