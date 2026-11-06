import time
from pathlib import Path

from aiogram import Router

from src.config import PROJECT_ROOT
from src.handlers.old_messages import OldMessagesMiddleware
from src.handlers.all import create_router as create_all_router
from src.handlers.basic import create_router as create_basic_router
from src.handlers.mem import create_router as create_mem_router
from src.handlers.mem.collector import PhotoCollectorMiddleware
from src.handlers.mem.meme_service import MemeService
from src.handlers.mem.meme_repository import MemeRepository
from src.handlers.mute import create_router as create_mute_router


def create_router(
    data_dir: Path | None = None, *, started_at: int | None = None,
    message_gate: OldMessagesMiddleware | None = None,
) -> Router:
    router = Router(name="handlers")
    directory = data_dir or PROJECT_ROOT / 'data'
    store = MemeRepository(directory / 'memes.sqlite3')
    service = MemeService(store)
    message_gate = message_gate or OldMessagesMiddleware(started_at if started_at is not None else int(time.time()))
    for observer in (router.message, router.edited_message):
        observer.outer_middleware(PhotoCollectorMiddleware(store))
        observer.outer_middleware(message_gate)

    router.startup.register(store.initialize)
    router.include_routers(create_mute_router(directory), create_mem_router(service), create_basic_router(), create_all_router())
    return router
