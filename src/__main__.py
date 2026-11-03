import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config import get_bot_token
from src.handlers import create_router


async def main() -> None:
    token = get_bot_token()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())

    async with Bot(token=token) as bot:
        await bot.set_my_commands([
            BotCommand(command="help", description="список команд"),
            BotCommand(command="all", description="тегнуть всех участников"),
            BotCommand(command="mute", description="замутить"),
            BotCommand(command="unmute", description="размутить"),
        ])
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

