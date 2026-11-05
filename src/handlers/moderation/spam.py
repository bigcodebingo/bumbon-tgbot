from collections import OrderedDict, deque
from dataclasses import dataclass, field

from aiogram.enums import ChatType, ContentType
from aiogram.filters import Filter
from aiogram.types import Message

REPEAT_LIMIT = 5
MESSAGES_PER_SECOND = 4
FLOOD_SECONDS = 3
WINDOW_SECONDS = 8
MAX_USERS = 4096
MAX_RECENT_EVENTS = max(REPEAT_LIMIT, MESSAGES_PER_SECOND * FLOOD_SECONDS)


CONTENT_TYPES = {
    ContentType.TEXT, ContentType.STICKER, ContentType.ANIMATION,
    ContentType.PHOTO, ContentType.VIDEO, ContentType.VIDEO_NOTE,
    ContentType.VOICE, ContentType.AUDIO, ContentType.DOCUMENT,
    ContentType.CONTACT, ContentType.LOCATION, ContentType.VENUE,
    ContentType.POLL, ContentType.DICE, ContentType.GAME,
}


@dataclass(frozen=True)
class SpamEvent:
    timestamp: float
    message_id: int
    text: str | None
    album_id: str | None


@dataclass
class SpamHistory:
    events: deque[SpamEvent] = field(default_factory=lambda: deque(maxlen=MAX_RECENT_EVENTS))
    second_counts: dict[int, int] = field(default_factory=dict)


class SpamFilter(Filter):
    def __init__(self) -> None:
        self.history: OrderedDict[tuple[int, int], SpamHistory] = OrderedDict()

    async def __call__(self, message: Message) -> bool | dict[str, str]:
        author = message.from_user
        if (message.chat.type != ChatType.SUPERGROUP or not author
                or author.is_bot or message.sender_chat or message.edit_date is not None
                or message.content_type not in CONTENT_TYPES):
            return False

        key = (message.chat.id, author.id)
        history = self.history.setdefault(key, SpamHistory())
        events = history.events
        self.history.move_to_end(key)
        while len(self.history) > MAX_USERS:
            self.history.popitem(last=False)

        timestamp = message.date.timestamp()
        newest = max(timestamp, events[-1].timestamp if events else timestamp)
        recent = [event for event in events if newest - event.timestamp <= WINDOW_SECONDS]
        events.clear()
        events.extend(recent)
        if newest - timestamp > WINDOW_SECONDS:
            return False
        if any(event.message_id == message.message_id for event in events):
            return False
        if message.media_group_id and any(event.album_id == message.media_group_id for event in events):
            return False

        text = message.text if message.content_type == ContentType.TEXT else None
        recent.append(SpamEvent(timestamp, message.message_id, text, message.media_group_id))
        events.clear()
        events.extend(sorted(recent, key=lambda event: (event.timestamp, event.message_id)))

        newest_second = int(newest)
        history.second_counts = {
            second: count for second, count in history.second_counts.items()
            if newest_second - FLOOD_SECONDS < second <= newest_second
        }
        second = int(timestamp)
        if newest_second - FLOOD_SECONDS < second <= newest_second:
            history.second_counts[second] = min(
                MESSAGES_PER_SECOND, history.second_counts.get(second, 0) + 1,
            )

        repeats = 0
        if text is not None:
            for event in reversed(events):
                if event.text == text:
                    repeats += 1
                else:
                    break
        if repeats >= REPEAT_LIMIT:
            return {"moderation_reason": "спам одинаковых сообщений"}
        if all(
            history.second_counts.get(newest_second - offset, 0) >= MESSAGES_PER_SECOND
            for offset in range(FLOOD_SECONDS)
        ):
            return {"moderation_reason": "слишком частую отправку сообщений"}
        return False
