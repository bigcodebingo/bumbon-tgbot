import logging
import sqlite3

from aiogram import BaseMiddleware
from aiogram.types import Message

from .meme_repository import MemeRepository
from .photo_filter import is_channel_forward

logger = logging.getLogger(__name__)


class PhotoCollectorMiddleware(BaseMiddleware):
    def __init__(self, store: MemeRepository) -> None:
        self.store = store

    async def __call__(self, handler, event: Message, data: dict):
        if event.photo and not (event.from_user and event.from_user.is_bot and not event.sender_chat):
            photo = event.photo[-1]
            try:
                if is_channel_forward(event):
                    await self.store.remove_photo(event.chat.id, event.message_id)
                else:
                    await self.store.add_photo(event.chat.id, event.message_id, photo.file_id, photo.file_unique_id)
            except (sqlite3.Error, OSError):
                logger.exception("Cannot collect photo %s:%s", event.chat.id, event.message_id)
        return await handler(event, data)
