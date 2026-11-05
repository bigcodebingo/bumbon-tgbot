import logging
import sqlite3

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

from .meme_service import MemeService

logger = logging.getLogger(__name__)


def create_router(service: MemeService) -> Router:
    router = Router(name="mem")

    @router.message(Command("mem"))
    async def random_photo(message: Message, bot: Bot) -> None:
        try:
            result, _ = await service.send_random(bot, chat_id=message.chat.id)
            if result == 'empty':
                await message.answer("в каталоге пока нет доступных фотографий")
            elif result == 'unavailable':
                await message.answer("выбранные старые фотографии недоступны, попробуйте /mem ещё раз")
        except TelegramAPIError:
            logger.exception("Cannot send random photo")
            await message.answer("не удалось отправить фотографию. проверьте доступ и права бота")
        except (sqlite3.Error, OSError):
            logger.exception("Cannot access photo catalog")
            await message.answer("не удалось прочитать или сохранить каталог фотографий")

    return router
