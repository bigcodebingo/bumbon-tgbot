import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMember, ChatPermissions, Message

from .permissions import (ADMIN_FIELDS, ALL_PERMISSIONS, NO_PERMISSIONS, MuteError,
                          check_bot_permissions, restore_admin_rights)
from .state import read_state, write_state

logger = logging.getLogger(__name__)
MUTE_FIELDS = ("chat_id", "user_id", "admin_rights", "stage", "until")


class MuteManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict] = {}
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        self.retry_after: dict[str, float] = {}

    @staticmethod
    def key(chat_id: int, user_id: int) -> str:
        return f"{chat_id}:{user_id}"

    async def start(self, bot: Bot) -> None:
        records = await read_state(self.path)
        if records:
            for key, record in records.items():
                if not isinstance(record, dict) or not set(MUTE_FIELDS) <= record.keys():
                    raise ValueError(f"Invalid mute snapshot: {key}")
                if (key != self.key(record["chat_id"], record["user_id"])
                        or type(record["chat_id"]) is not int
                        or type(record["user_id"]) is not int
                        or record["stage"] not in {"prepared", "active", "restoring"}
                        or type(record["until"]) is not int):
                    raise ValueError(f"Invalid mute snapshot: {key}")
                rights = record["admin_rights"]
                if rights is not None and (
                    not isinstance(rights, dict) or set(rights) != set(ADMIN_FIELDS)
                    or any(not isinstance(value, bool) for value in rights.values())
                ):
                    raise ValueError(f"Invalid administrator rights: {key}")
            self.records = {
                key: {field: record[field] for field in MUTE_FIELDS}
                for key, record in records.items()
            }
        self.task = asyncio.create_task(self.recovery_loop(bot))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def save(self, key: str, record: dict | None) -> None:
        updated = dict(self.records)
        if record is None:
            updated.pop(key, None)
        else:
            updated[key] = record
        await write_state(self.path, updated)
        self.records = updated
        if record is None:
            self.retry_after.pop(key, None)

    async def resolve_target(self, message: Message, bot: Bot, username: str | None) -> int:
        reply = message.reply_to_message
        if reply:
            if not reply.from_user or reply.sender_chat:
                raise MuteError("ответьте на сообщение, отправленное пользователем от своего имени")
            if username and (reply.from_user.username or "").lower() != username:
                raise MuteError("@username не совпадает с автором сообщения, на которое вы ответили")
            return reply.from_user.id
        if not username:
            raise MuteError("ответьте на сообщение пользователя или укажите @username")
        for admin in await bot.get_chat_administrators(message.chat.id):
            if (admin.user.username or "").lower() == username:
                return admin.user.id
        for record in self.records.values():
            if record["chat_id"] != message.chat.id:
                continue
            current = await bot.get_chat_member(message.chat.id, record["user_id"])
            if (current.user.username or "").lower() == username:
                return current.user.id
        raise MuteError("не нашёл @username среди администраторов и сохранённых мутов")

    async def restore(self, bot: Bot, key: str) -> bool:
        record = self.records[key]
        chat_id, user_id = record["chat_id"], record["user_id"]
        current = await bot.get_chat_member(chat_id, user_id)
        if current.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} or (
            current.status == ChatMemberStatus.RESTRICTED and not current.is_member
        ):
            logger.warning("Discarding mute for departed member %s in %s", user_id, chat_id)
            await self.save(key, None)
            return False
        if current.status == ChatMemberStatus.CREATOR:
            await self.save(key, None)
            return False
        await check_bot_permissions(bot, chat_id, record["admin_rights"])
        if current.status != ChatMemberStatus.ADMINISTRATOR:
            await bot.restrict_chat_member(
                chat_id, user_id, permissions=ChatPermissions(**ALL_PERMISSIONS),
                until_date=0, use_independent_chat_permissions=True,
            )
        if record["admin_rights"] is not None:
            await restore_admin_rights(bot, chat_id, user_id, record["admin_rights"])
        await self.save(key, None)
        return True

    async def mute(self, bot: Bot, chat_id: int, target: ChatMember, minutes: int) -> None:
        user_id = target.user.id
        if target.user.is_bot or target.status == ChatMemberStatus.CREATOR:
            raise MuteError("владельца беседы и ботов мутить нельзя")
        if target.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} or (
            target.status == ChatMemberStatus.RESTRICTED and not target.is_member
        ):
            raise MuteError("пользователь не состоит в беседе")
        key = self.key(chat_id, user_id)
        previous = self.records.get(key)
        if previous and previous["stage"] != "active":
            raise MuteError("ещё не завершено восстановление предыдущих прав.\n\n используйте /unmute")
        if target.status == ChatMemberStatus.RESTRICTED and not previous:
            raise MuteError("у пользователя уже есть ограничения вне этого бота.\n\n Сначала снимите их через /unmute")
        rights = None
        if target.status == ChatMemberStatus.ADMINISTRATOR:
            if not target.can_be_edited:
                raise MuteError("telegram не разрешает менять права этого администратора через бота")
            rights = {name: bool(getattr(target, name, False)) for name in ADMIN_FIELDS}
        await check_bot_permissions(bot, chat_id, previous["admin_rights"] if previous else rights)
        until = int(time.time()) + minutes * 60
        if previous:
            record = {**previous, "until": until}
        else:
            record = {
                "chat_id": chat_id, "user_id": user_id,
                "admin_rights": rights,
                "until": until,
            }
        await self.save(key, {**record, "stage": "prepared"})
        try:
            if rights is not None:
                await bot.promote_chat_member(
                    chat_id, user_id, **{name: False for name in ADMIN_FIELDS},
                )
            await bot.restrict_chat_member(
                chat_id, user_id, permissions=ChatPermissions(**NO_PERMISSIONS),
                until_date=until, use_independent_chat_permissions=True,
            )
            await self.save(key, {**record, "stage": "active"})
        except Exception:
            try:
                await self.restore(bot, key)
            except Exception:
                logger.exception("Mute rollback pending for %s", key)
            raise

    async def unmute(self, bot: Bot, chat_id: int, target: ChatMember) -> bool:
        key = self.key(chat_id, target.user.id)
        if key in self.records:
            await self.save(key, {**self.records[key], "stage": "restoring"})
            return await self.restore(bot, key)
        if target.status != ChatMemberStatus.RESTRICTED or not target.is_member:
            raise MuteError("у пользователя нет действующего мута")
        await check_bot_permissions(bot, chat_id)
        await bot.restrict_chat_member(
            chat_id, target.user.id, permissions=ChatPermissions(**ALL_PERMISSIONS),
            use_independent_chat_permissions=True, until_date=0,
        )
        return True

    async def recover_due(self, bot: Bot) -> None:
        async with self.lock:
            for key, record in list(self.records.items()):
                if record["stage"] == "active" and record["until"] > time.time():
                    continue
                if self.retry_after.get(key, 0) > time.time():
                    continue
                try:
                    await self.restore(bot, key)
                except Exception:
                    logger.exception("Cannot restore mute %s; snapshot retained for retry", key)
                    first_failure = key not in self.retry_after
                    self.retry_after[key] = time.time() + 30
                    if first_failure:
                        try:
                            await bot.send_message(
                                record["chat_id"],
                                f"не удалось автоматически восстановить права пользователя "
                                f"{record['user_id']}\n\n"
                                "данные сохранены, восстановление будет повторено. проверьте права бота",
                            )
                        except TelegramAPIError:
                            logger.exception("Cannot notify chat about pending mute recovery")

    async def recovery_loop(self, bot: Bot) -> None:
        while True:
            await self.recover_due(bot)
            await asyncio.sleep(5)

