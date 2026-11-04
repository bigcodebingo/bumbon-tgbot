import logging
import time
from pathlib import Path

from aiogram import Bot, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from .manager import MuteManager
from .permissions import (
    ADMIN_PROFILE, MuteError, check_bot_permissions, member_present,
    require_owner, restore_admin,
)
from .state import read_state, write_state

logger = logging.getLogger(__name__)


class AdminSetup:
    def __init__(self, path: Path, mutes: MuteManager) -> None:
        self.path = path
        self.mutes = mutes
        self.snapshots: dict[str, dict] = {}

    async def start(self) -> None:
        snapshots = await read_state(self.path)
        for chat_id, snapshot in snapshots.items():
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("members"), dict):
                raise ValueError(f"Invalid administrator snapshot: {chat_id}")
            if str(snapshot.get("chat_id")) != chat_id or type(snapshot["chat_id"]) is not int:
                raise ValueError(f"Invalid administrator chat: {chat_id}")
            for user_id, member in snapshot["members"].items():
                if (not isinstance(member, dict) or type(member.get("user_id")) is not int
                        or str(member["user_id"]) != user_id
                        or not isinstance(member.get("full_name"), str)
                        or "title" not in member
                        or (member["title"] is not None and not isinstance(member["title"], str))
                        or member.get("stage") not in {"saved", "promoting", "restored"}):
                    raise ValueError(f"Invalid saved administrator: {chat_id}:{user_id}")
        self.snapshots = snapshots

    async def save_chat(self, chat_id: int, snapshot: dict) -> None:
        updated = {**self.snapshots, str(chat_id): snapshot}
        await write_state(self.path, updated)
        self.snapshots = updated

    def check_no_mutes(self, chat_id: int) -> None:
        if any(record["chat_id"] == chat_id for record in self.mutes.records.values()):
            raise MuteError("сначала завершите действующие муты и восстановление прав")

    async def capture(self, bot: Bot, chat_id: int) -> int:
        self.check_no_mutes(chat_id)
        previous = self.snapshots.get(str(chat_id))
        if previous and any(m["stage"] != "restored" for m in previous["members"].values()):
            raise MuteError(
                "есть незавершённое сохранение. используйте /restoreadmins"
            )
        members = {
            str(admin.user.id): {
                "user_id": admin.user.id,
                "full_name": admin.user.full_name,
                "title": admin.custom_title,
                "stage": "saved",
            }
            for admin in await bot.get_chat_administrators(chat_id)
            if admin.status == ChatMemberStatus.ADMINISTRATOR and not admin.user.is_bot
        }
        if not members:
            raise MuteError("нет администраторов для сохранения")
        await self.save_chat(chat_id, {
            "chat_id": chat_id, "saved_at": int(time.time()), "members": members,
        })
        return len(members)

    async def restore(self, bot: Bot, chat_id: int) -> tuple[int, int, list[str]]:
        self.check_no_mutes(chat_id)
        snapshot = self.snapshots.get(str(chat_id))
        if not snapshot or not snapshot["members"]:
            raise MuteError("нет сохранений. выполните /saveadmins до ручного снятия админок")
        await check_bot_permissions(bot, chat_id, ADMIN_PROFILE)
        restored, already_done, errors = 0, 0, []
        for user_key in list(snapshot["members"]):
            saved = snapshot["members"][user_key]
            if saved["stage"] == "restored":
                already_done += 1
                continue
            try:
                current = await bot.get_chat_member(chat_id, saved["user_id"])
                if not member_present(current) or current.status == ChatMemberStatus.CREATOR:
                    raise MuteError("пользователь вышел, заблокирован или стал владельцем; права не изменены")
                if current.status == ChatMemberStatus.RESTRICTED:
                    raise MuteError("сначала снимите действующие ограничения пользователя.")
                if current.status == ChatMemberStatus.ADMINISTRATOR and not current.can_be_edited:
                    raise MuteError("владелец должен вручную разжаловать админа, затем повторить /restoreadmins")

                members = {**snapshot["members"], user_key: {**saved, "stage": "promoting"}}
                snapshot = {**snapshot, "members": members}
                await self.save_chat(chat_id, snapshot)
                await restore_admin(bot, chat_id, saved["user_id"], ADMIN_PROFILE, saved["title"])
                members = {**members, user_key: {**saved, "stage": "restored"}}
                snapshot = {**snapshot, "members": members}
                await self.save_chat(chat_id, snapshot)
                restored += 1
            except (MuteError, TelegramAPIError) as error:
                logger.warning("Cannot restore saved admin %s:%s: %s", chat_id, user_key, error)
                reason = str(error) if isinstance(error, MuteError) else "ошибка telegram; повторите /restoreadmins"
                errors.append(f"{saved['full_name']} ({saved['user_id']}): {reason}"[:220])
        return restored, already_done, errors


def create_router(setup: AdminSetup) -> Router:
    router = Router(name="admin_setup")

    @router.message(Command("saveadmins", "restoreadmins"))
    async def configure_admins(message: Message, bot: Bot, command: CommandObject) -> None:
        try:
            await require_owner(message, bot)
            if command.args:
                raise MuteError("для этих команд списки не нужны")
            async with setup.mutes.lock:
                if command.command == "saveadmins":
                    count = await setup.capture(bot, message.chat.id)
                    text = (
                        f"зафиксировано администраторов: {count}\n"
                        "необходимо вручную разжаловать пользователей\n\n затем выполнить /restoreadmins\n"
                    )
                else:
                    restored, done, errors = await setup.restore(bot, message.chat.id)
                    text = f"восстановлено администраторов: {restored}.\n\nуже восстановлено ранее: {done}"
                    if errors:
                        text += f"\nне завершено: {len(errors)}.\n" + "\n".join(errors[:10])
                        text += "\nсписок сохранён. исправьте причины и повторите /restoreadmins"
            await message.answer(text)
        except MuteError as error:
            await message.answer(str(error))
        except TelegramAPIError:
            logger.exception("Administrator setup failed in chat %s", message.chat.id)
            await message.answer("не удалось выполнить запрос telegram. проверьте права бота")
        except OSError:
            logger.exception("Cannot persist administrator setup")
            await message.answer("не удалось сохранить данные. проверьте доступ к папке data и повторите команду")

    return router
