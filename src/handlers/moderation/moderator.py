import logging
import time
from collections import OrderedDict

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Message

from src.handlers.mute.manager import MuteManager
from src.handlers.mute.permissions import MuteError

from .insults import InsultFilter
from .spam import SpamFilter

logger = logging.getLogger(__name__)

def create_router(manager: MuteManager) -> Router:
    router = Router(name="automatic_moderation")
    rules = InsultFilter()
    pending: set[tuple[int, int]] = set()
    retry_after: OrderedDict[tuple[int, int], float] = OrderedDict()

    def postpone(key: tuple[int, int], delay: float = 5) -> None:
        retry_after[key] = time.monotonic() + max(5, delay)
        retry_after.move_to_end(key)
        while len(retry_after) > 4096:
            retry_after.popitem(last=False)

    async def moderate(message: Message, bot: Bot, moderation_reason: str) -> None:
        key = (message.chat.id, message.from_user.id)
        if key in pending or retry_after.get(key, 0) > time.monotonic():
            return
        pending.add(key)
        try:
            async with manager.lock:
                record = manager.records.get(manager.key(message.chat.id, message.from_user.id))
                if record and (record["stage"] != "active" or record["until"] > time.time()):
                    return
                target = await bot.get_chat_member(message.chat.id, message.from_user.id)
                if target.status == ChatMemberStatus.CREATOR:
                    postpone(key)
                    return
                await manager.mute(bot, message.chat.id, target, 1)
            await message.answer(
                f"🔇 {message.from_user.full_name} получил мут на 1 минуту за {moderation_reason}"
            )
        except MuteError as error:
            postpone(key)
            logger.warning("Automatic mute refused in chat %s: %s", message.chat.id, error)
        except TelegramRetryAfter as error:
            postpone(key, error.retry_after)
            logger.warning("Automatic mute rate-limited in chat %s; retry after %s seconds",
                           message.chat.id, error.retry_after)
        except (TelegramAPIError, OSError):
            postpone(key)
            logger.exception("Automatic mute failed in chat %s", message.chat.id)
        finally:
            pending.discard(key)

    router.message.register(moderate, SpamFilter())
    router.message.register(moderate, rules)
    router.edited_message.register(moderate, rules)
    return router
