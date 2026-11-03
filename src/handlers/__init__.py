from aiogram import Router

from src.handlers.all import create_router as create_all_router
from src.handlers.basic import create_router as create_basic_router


def create_router() -> Router:
    router = Router(name="handlers")
    router.include_routers(create_basic_router(), create_all_router())
    return router
