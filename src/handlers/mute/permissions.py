from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.methods import PromoteChatMember
from aiogram.types import ChatMember, ChatPermissions, Message

ADMIN_FIELDS = tuple(
    name for name in PromoteChatMember.model_fields
    if name.startswith("can_") or name == "is_anonymous"
)
ALL_PERMISSIONS = {name: True for name in ChatPermissions.model_fields}
NO_PERMISSIONS = {name: False for name in ChatPermissions.model_fields}

ADMIN_PROFILE = dict.fromkeys(ADMIN_FIELDS, False)
ADMIN_PROFILE.update(
    can_manage_chat=True,
    can_change_info=True,
    can_restrict_members=True,
    can_pin_messages=True,
    can_manage_video_chats=True,
    can_post_stories=True,
    can_edit_stories=True,
    can_delete_stories=True,
)


class MuteError(Exception):
    """An expected refusal that can be shown to the command's author."""


async def _get_caller(message: Message, bot: Bot) -> ChatMember:
    if message.chat.type != ChatType.SUPERGROUP:
        raise MuteError("команды модерации работают только в супергруппе telegram")
    if not message.from_user or message.sender_chat:
        raise MuteError("вызови команду от своего имени")
    return await bot.get_chat_member(message.chat.id, message.from_user.id)


async def require_owner(message: Message, bot: Bot) -> None:
    caller = await _get_caller(message, bot)
    if caller.status != ChatMemberStatus.CREATOR:
        raise MuteError("команды модерации доступны только владельцу беседы")


async def require_moderator(message: Message, bot: Bot) -> ChatMember:
    caller = await _get_caller(message, bot)
    if caller.status == ChatMemberStatus.CREATOR:
        return caller
    if caller.status == ChatMemberStatus.ADMINISTRATOR and caller.can_restrict_members:
        return caller
    raise MuteError("команды мута доступны владельцу и администраторам с правом ограничивать участников")


def check_moderation_target(caller: ChatMember, target: ChatMember, *, was_admin: bool = False) -> None:
    if caller.status == ChatMemberStatus.CREATOR:
        return
    if was_admin or target.status not in {ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED}:
        raise MuteError("администратор может выдавать и снимать мут только обычным участникам")


def member_present(member: ChatMember) -> bool:
    if member.status == ChatMemberStatus.RESTRICTED:
        return member.is_member
    return member.status in {
        ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR,
    }


async def check_bot_permissions(bot: Bot, chat_id: int, rights: dict | None = None) -> None:
    own = await bot.get_chat_member(chat_id, bot.id)
    if own.status != ChatMemberStatus.ADMINISTRATOR or not own.can_restrict_members:
        raise MuteError("бот должен иметь права администратора и на блокировку пользователей")
    if rights is not None:
        if not own.can_promote_members:
            raise MuteError("бот должен иметь право на добавление администраторов")
        missing = [name for name, enabled in rights.items()
                   if enabled and name != "is_anonymous" and not getattr(own, name, False)]
        if missing:
            raise MuteError("у бота недостаточно прав: " + ", ".join(missing))


async def restore_admin_rights(bot: Bot, chat_id: int, user_id: int, rights: dict,) -> None:
    current = await bot.get_chat_member(chat_id, user_id)
    if current.status == ChatMemberStatus.ADMINISTRATOR and not current.can_be_edited:
        raise MuteError("необходимо сперва вручную разжаловать пользователя")
    await bot.promote_chat_member(chat_id, user_id, **rights)
    restored = await bot.get_chat_member(chat_id, user_id)
    if (restored.status != ChatMemberStatus.ADMINISTRATOR or not restored.can_be_edited
            or any(bool(getattr(restored, name, False)) != enabled for name, enabled in rights.items())):
        raise MuteError("telegram не подтвердил права или управление администратором. данные сохранены")


async def restore_admin(bot: Bot, chat_id: int, user_id: int, rights: dict, title: str | None,) -> None:
    await restore_admin_rights(bot, chat_id, user_id, rights)
    await bot.set_chat_administrator_custom_title(chat_id, user_id, custom_title=title or "")
    restored = await bot.get_chat_member(chat_id, user_id)
    if (restored.custom_title or "") != (title or ""):
        raise MuteError("telegram не подтвердил восстановление титула. данные сохранены")
