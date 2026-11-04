import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .manager import MuteManager
from .permissions import MuteError, require_owner

logger = logging.getLogger(__name__)


def parse_arguments(args: str | None, unmute: bool) -> tuple[str | None, int]:
    parts = (args or "").split()
    username = parts.pop(0)[1:].lower() if parts and parts[0].startswith("@") else None
    if username == "":
        raise MuteError("после @ укажите username пользователя")
    if unmute:
        if parts:
            raise MuteError("для /unmute нельзя указывать время")
        return username, 0
    if len(parts) > 1 or (parts and (not parts[0].isascii() or not parts[0].isdigit())):
        raise MuteError("формат: /mute [@username] [минуты], либо ответ на сообщение")
    minutes = int(parts[0]) if parts else 1
    if not 1 <= minutes <= 525600:
        raise MuteError("укажите от 1 до 525600 минут (365 дней)")
    return username, minutes



def create_router(manager: MuteManager) -> Router:
    router = Router(name="mute_commands")

    @router.message(Command("mute", "unmute"))
    async def moderate(message: Message, bot: Bot, command: CommandObject) -> None:
        try:
            await require_owner(message, bot)
            unmute = command.command == "unmute"
            username, minutes = parse_arguments(command.args, unmute)
            async with manager.lock:
                user_id = await manager.resolve_target(message, bot, username)
                target = await bot.get_chat_member(message.chat.id, user_id)
                if unmute:
                    snapshot = manager.records.get(manager.key(message.chat.id, user_id))
                    restored = await manager.unmute(bot, message.chat.id, target)
                    if restored:
                        text = f"🔉 {target.user.full_name} размучен"
                        if snapshot and snapshot["admin_rights"] is not None:
                            text += " прежние права администратора восстановлены"
                    else:
                        text = "пользователь вышел из беседы или стал её владельцем; права не изменены"
                else:
                    await manager.mute(bot, message.chat.id, target, minutes)
                    if 11 <= minutes % 100 <= 14:
                        minute_word = "минут"
                    elif minutes % 10 == 1:
                        minute_word = "минуту"
                    elif 2 <= minutes % 10 <= 4:
                        minute_word = "минуты"
                    else:
                        minute_word = "минут"
                    text = f"🔇 {target.user.full_name} получил мут на {minutes} {minute_word}"
            await message.answer(text)
        except MuteError as error:
            await message.answer(str(error))
        except TelegramAPIError:
            logger.exception("Telegram moderation error in chat %s", message.chat.id)
            await message.answer(
                "telegram не позволил завершить действие. проверьте права бота"
                "если права уже менялись, сохранённые данные остаются до завершения восстановления"
            )
        except OSError:
            logger.exception("Cannot save mute state")
            await message.answer("не удалось сохранить данные мута. проверьте доступ к папке data")

    return router
