import asyncio
import logging
from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from .meme_repository import MemeRepository

logger = logging.getLogger(__name__)


class MemeService:
    def __init__(self, store: MemeRepository) -> None:
        self.store = store
        self.lock = asyncio.Lock()

    async def send_random(
        self, bot: Bot, *, chat_id: int, can_send: Callable[[], bool] | None = None,
    ) -> tuple[str, int | None]:
        async with self.lock:
            for _ in range(10):
                photo = await self.store.choose(chat_id)
                if photo is None:
                    return "empty", None
                if can_send is not None and not can_send():
                    return "deferred", None
                try:
                    if photo['file_id']:
                        sent = await bot.send_photo(chat_id=chat_id, photo=photo['file_id'])
                    else:
                        sent = await bot.copy_message(
                            chat_id=chat_id, from_chat_id=photo['chat_id'],
                            message_id=photo['message_id'], caption="",
                        )
                except TelegramBadRequest as error:
                    if not photo['file_id'] and "message to copy not found" in error.message.casefold():
                        await self.store.update_photo(chat_id, photo['message_id'], unavailable=True)
                        logger.warning("Archived photo message %s is unavailable", photo['message_id'])
                        continue
                    raise
                await self.store.update_photo(chat_id, photo['message_id'])
                return "sent", sent.message_id
            return "unavailable", None
