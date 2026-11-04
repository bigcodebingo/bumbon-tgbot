from pathlib import Path

from aiogram import Bot, Router

from src.config import PROJECT_ROOT

from .admins import AdminSetup, create_router as create_admin_router
from .commands import create_router as create_mute_router
from .manager import MuteManager


def create_router(data_dir: Path | None = None) -> Router:
    directory = data_dir or PROJECT_ROOT / "data"
    manager = MuteManager(directory / "mutes.json")
    setup = AdminSetup(directory / "admins.json", manager)
    router = Router(name="mute")

    async def startup(bot: Bot) -> None:
        await setup.start()
        await manager.start(bot)

    router.startup.register(startup)
    router.shutdown.register(manager.stop)
    router.include_routers(create_admin_router(setup), create_mute_router(manager))
    return router
