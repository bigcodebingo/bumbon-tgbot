import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeChat

from src.config import MEM_CHAT_ID, get_bot_token
from src.handlers import create_router


async def main() -> None:
    token = get_bot_token()
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())

    async with Bot(token=token) as bot:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=MEM_CHAT_ID))
        await bot.set_my_commands([
            BotCommand(command="help", description="список команд"),
            BotCommand(command="all", description="тегнуть всех участников"),
            BotCommand(command="mute", description="замутить"),
            BotCommand(command="unmute", description="размутить"),
            BotCommand(command="mem", description="случайная фотография"),
        ])
        await bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types(),
            tasks_concurrency_limit=32,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

