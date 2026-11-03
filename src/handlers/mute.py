import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.methods import PromoteChatMember
from aiogram.types import ChatMember, ChatPermissions, Message

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)
ADMIN_FIELDS = tuple(
    name for name in PromoteChatMember.model_fields
    if name.startswith("can_") or name == "is_anonymous"
)
ALL_PERMISSIONS = {name: True for name in ChatPermissions.model_fields}
NO_PERMISSIONS = {name: False for name in ChatPermissions.model_fields}


class MuteError(Exception):
    """An expected refusal that can be shown to the command's author."""


def parse_arguments(args: str | None, unmute: bool) -> tuple[str | None, int]:
    parts = (args or "").split()
    username = parts.pop(0)[1:].lower() if parts and parts[0].startswith("@") else None
    if username == "":
        raise MuteError("после @ укажите username пользователя.")
    if unmute:
        if parts:
            raise MuteError("/unmute снимает мут сразу. Число минут не нужно.")
        return username, 0
    if len(parts) > 1 or (parts and (not parts[0].isascii() or not parts[0].isdigit())):
        raise MuteError("формат: /mute [@username] [минуты], либо ответ на сообщение.")
    if parts and len(parts[0]) > 6:
        raise MuteError("укажите от 1 до 525600 минут (365 дней).")
    minutes = int(parts[0]) if parts else 1
    if not 1 <= minutes <= 525600:
        raise MuteError("укажите от 1 до 525600 минут (365 дней).")
    return username, minutes


class MuteManager:
    """Persist original rights before changing Telegram state and retry recovery."""

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
        if self.path.exists():
            # Invalid state must stop startup instead of silently losing rights.
            records = json.loads(await asyncio.to_thread(self.path.read_text, encoding="utf-8"))
            if not isinstance(records, dict):
                raise ValueError("Invalid mute state: expected an object")
            for key, record in records.items():
                if not isinstance(record, dict) or not {
                    "chat_id", "user_id", "admin_rights", "custom_title", "stage", "until",
                } <= record.keys():
                    raise ValueError(f"Invalid mute snapshot: {key}")
                if (key != self.key(record["chat_id"], record["user_id"])
                        or type(record["chat_id"]) is not int
                        or type(record["user_id"]) is not int
                        or record["stage"] not in {"prepared", "active", "restoring"}
                        or type(record["until"]) is not int
                        or (record["custom_title"] is not None and not isinstance(record["custom_title"], str))):
                    raise ValueError(f"Invalid mute snapshot: {key}")
                rights = record["admin_rights"]
                if rights is not None and (
                    not isinstance(rights, dict) or set(rights) != set(ADMIN_FIELDS)
                    or any(not isinstance(value, bool) for value in rights.values())
                ):
                    raise ValueError(f"Invalid administrator rights: {key}")
            self.records = records
        self.task = asyncio.create_task(self.recovery_loop(bot))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def _write(self, records: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    async def save(self, key: str, record: dict | None) -> None:
        updated = dict(self.records)
        if record is None:
            updated.pop(key, None)
        else:
            updated[key] = record
        await asyncio.to_thread(self._write, updated)
        self.records = updated
        if record is None:
            self.retry_after.pop(key, None)

    async def check_bot(self, bot: Bot, chat_id: int, rights: dict | None = None) -> None:
        own = await bot.get_chat_member(chat_id, bot.id)
        if own.status != ChatMemberStatus.ADMINISTRATOR or not own.can_restrict_members:
            raise MuteError("боту нужны права администратора и «Блокировка участников».")
        if rights is not None:
            if not own.can_promote_members:
                raise MuteError("для мута администратора боту нужно право «Добавление администраторов».")
            missing = [name for name, enabled in rights.items()
                       if enabled and name != "is_anonymous" and not getattr(own, name, False)]
            if missing:
                raise MuteError(
                    "бот не сможет восстановить все права этого администратора. "
                    "Сначала выдайте боту соответствующие права: " + ", ".join(missing)
                )

    async def resolve_target(self, message: Message, bot: Bot, username: str | None) -> int:
        reply = message.reply_to_message
        if reply:
            if not reply.from_user or reply.sender_chat:
                raise MuteError("ответьте на сообщение, отправленное пользователем от своего имени.")
            if username and (reply.from_user.username or "").lower() != username:
                raise MuteError("username не совпадает с автором сообщения, на которое вы ответили.")
            return reply.from_user.id
        if not username:
            raise MuteError("ответьте на сообщение пользователя или укажите @username.")
        for admin in await bot.get_chat_administrators(message.chat.id):
            if (admin.user.username or "").lower() == username:
                return admin.user.id
        for record in self.records.values():
            if record["chat_id"] != message.chat.id:
                continue
            current = await bot.get_chat_member(message.chat.id, record["user_id"])
            if (current.user.username or "").lower() == username:
                return current.user.id
        raise MuteError("не нашёл @username среди администраторов и сохранённых мутов. Ответьте на сообщение пользователя.")

    async def restore(self, bot: Bot, key: str) -> bool:
        record = self.records[key]
        chat_id, user_id = record["chat_id"], record["user_id"]
        current = await bot.get_chat_member(chat_id, user_id)
        if current.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} or (
            current.status == ChatMemberStatus.RESTRICTED and not current.is_member
        ):
            # Never promote someone who left or was banned by another admin.
            logger.warning("Discarding mute for departed member %s in %s", user_id, chat_id)
            await self.save(key, None)
            return False
        if current.status == ChatMemberStatus.CREATOR:
            await self.save(key, None)
            return False
        await self.check_bot(bot, chat_id, record["admin_rights"])
        if current.status != ChatMemberStatus.ADMINISTRATOR:
            await bot.restrict_chat_member(
                chat_id, user_id, permissions=ChatPermissions(**ALL_PERMISSIONS),
                until_date=0, use_independent_chat_permissions=True,
            )
        if record["admin_rights"] is not None:
            await bot.promote_chat_member(chat_id, user_id, **record["admin_rights"])
            await bot.set_chat_administrator_custom_title(
                chat_id, user_id, custom_title=record["custom_title"] or "",
            )
            restored = await bot.get_chat_member(chat_id, user_id)
            if (restored.status != ChatMemberStatus.ADMINISTRATOR
                    or any(bool(getattr(restored, name, False)) != enabled
                           for name, enabled in record["admin_rights"].items())
                    or (restored.custom_title or "") != (record["custom_title"] or "")):
                raise MuteError("Telegram не подтвердил полное восстановление прав и титула. Данные сохранены.")
        # Keep the snapshot if any API call above failed, including title restore.
        await self.save(key, None)
        return True

    async def mute(self, bot: Bot, chat_id: int, target: ChatMember, minutes: int) -> None:
        user_id = target.user.id
        if target.user.is_bot or target.status == ChatMemberStatus.CREATOR:
            raise MuteError("владельца беседы и ботов мутить нельзя.")
        if target.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED} or (
            target.status == ChatMemberStatus.RESTRICTED and not target.is_member
        ):
            raise MuteError("пользователь не состоит в беседе.")
        key = self.key(chat_id, user_id)
        previous = self.records.get(key)
        if previous and previous["stage"] != "active":
            raise MuteError("ещё не завершено восстановление предыдущих прав. Используйте /unmute.")
        if target.status == ChatMemberStatus.RESTRICTED and not previous:
            raise MuteError("у пользователя уже есть ограничения вне этого бота. Сначала снимите их через /unmute.")
        rights = None
        if target.status == ChatMemberStatus.ADMINISTRATOR:
            if not target.can_be_edited:
                raise MuteError("Telegram не разрешает боту менять права этого администратора.")
            rights = {name: bool(getattr(target, name, False)) for name in ADMIN_FIELDS}
        await self.check_bot(bot, chat_id, previous["admin_rights"] if previous else rights)
        until = int(time.time()) + minutes * 60
        if previous:
            # Repeated /mute changes the deadline, never the original snapshot.
            record = {**previous, "until": until}
        else:
            record = {
                "chat_id": chat_id, "user_id": user_id,
                "admin_rights": rights, "custom_title": getattr(target, "custom_title", None),
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
            # A failed request can have succeeded on Telegram before disconnect.
            # Recovery is also necessary when demotion succeeds but mute fails.
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
            raise MuteError("у пользователя нет мута, который можно снять.")
        await self.check_bot(bot, chat_id)
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
                                f"{record['user_id']}. Проверьте права бота. "
                                "Данные сохранены, восстановление будет повторено.",
                            )
                        except TelegramAPIError:
                            logger.exception("Cannot notify chat about pending mute recovery")

    async def recovery_loop(self, bot: Bot) -> None:
        while True:
            await self.recover_due(bot)
            await asyncio.sleep(5)


def create_router(state_path: Path | None = None) -> Router:
    router = Router(name="mute")
    manager = MuteManager(state_path or PROJECT_ROOT / "data" / "mutes.json")
    router.startup.register(manager.start)
    router.shutdown.register(manager.stop)

    @router.message(Command("mute", "unmute"))
    async def moderate(message: Message, bot: Bot, command: CommandObject) -> None:
        if message.chat.type != ChatType.SUPERGROUP:
            await message.answer("мут работает только в супергруппе Telegram.")
            return
        if not message.from_user or message.sender_chat:
            await message.answer("вызови команду от своего имени.")
            return
        try:
            caller = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if caller.status != ChatMemberStatus.CREATOR:
                raise MuteError("как и раньше, /mute и /unmute доступны только владельцу беседы.")
            unmute = command.command == "unmute"
            username, minutes = parse_arguments(command.args, unmute)
            async with manager.lock:
                user_id = await manager.resolve_target(message, bot, username)
                target = await bot.get_chat_member(message.chat.id, user_id)
                if unmute:
                    snapshot = manager.records.get(manager.key(message.chat.id, user_id))
                    restored = await manager.unmute(bot, message.chat.id, target)
                    if restored:
                        text = f"🔉 {target.user.full_name}: мут снят."
                        if snapshot and snapshot["admin_rights"] is not None:
                            text += " Прежние права и титул администратора восстановлены."
                    else:
                        text = "пользователь вышел из беседы или стал её владельцем; права не изменены."
                else:
                    await manager.mute(bot, message.chat.id, target, minutes)
                    text = f"🔇 {target.user.full_name}: мут на {minutes} мин."
            await message.answer(text)
        except MuteError as error:
            await message.answer(str(error))
        except TelegramAPIError:
            logger.exception("Telegram moderation error in chat %s", message.chat.id)
            await message.answer(
                "Telegram не позволил завершить действие. Проверьте права бота. "
                "Если права уже менялись, сохранённые данные остаются до завершения восстановления."
            )
        except OSError:
            logger.exception("Cannot save mute state")
            await message.answer("не удалось сохранить данные мута. Проверьте доступ к папке data.")

    return router
