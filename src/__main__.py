import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from src.config import get_bot_token
from src.handlers import create_router
from src.handlers.old_messages import OldMessagesMiddleware
from src.polling_recovery import PollingRecoveryMiddleware


async def main() -> None:
    token = get_bot_token()
    dispatcher = Dispatcher()
    message_gate = OldMessagesMiddleware(int(time.time()) + 1)
    dispatcher.include_router(create_router(message_gate=message_gate))

    async with Bot(token=token) as bot:
        bot.session.middleware(PollingRecoveryMiddleware(message_gate))
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

