import asyncio
import logging

from aiogram import Bot, Dispatcher

from bumbon.config import get_bot_token


async def main() -> None:
    bot = Bot(token=get_bot_token())
    dispatcher = Dispatcher()

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

