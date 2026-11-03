import logging
from html import escape

from aiogram import Bot, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import Message

logger = logging.getLogger(__name__)


def create_router() -> Router:
    router = Router(name="all")

    @router.message(Command("all"))
    async def tag_all(message: Message, bot: Bot) -> None:
        if message.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            await message.answer("команда /all работает только в беседе.")
            return
        if not message.from_user or message.sender_chat:
            await message.answer(
                "вызови /all от своего имени."
            )
            return

        chat_id = message.chat.id
        try:
            admins = await bot.get_chat_administrators(chat_id)
        except TelegramAPIError:
            logger.exception("Could not get administrators of chat %s", chat_id)
            await message.answer(
                "не удалось получить список участников."
            )
            return

        participants = [
            admin.user
            for admin in admins
            if not admin.user.is_bot
            and admin.user.id not in {bot.id, message.from_user.id}
        ]
        if not participants:
            await message.answer(
                "нет других участников."
            )
            return
        mentions = [
            f"@{user.username}" if user.username else
            f'<a href="tg://user?id={user.id}">{escape(user.full_name)}</a>'
            for user in participants
        ]
        text = " ".join(mentions)
        await message.answer(text, parse_mode=ParseMode.HTML)

    return router
