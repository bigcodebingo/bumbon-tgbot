import time
import unicodedata
from collections import OrderedDict, deque

from aiogram.enums import ChatType, MessageEntityType
from aiogram.filters import Filter
from aiogram.types import Message

from .bad_words import (
    ADDRESS, CLAUSE_SEPARATOR, IMPERATIVE, INSULT, NEGATED_INSULT,
    OTHER_SUBJECT, QUOTES, REPORT, STORY, WORD,
)

HISTORY_SECONDS = 90
MAX_CHATS = 128
MAX_MESSAGES = 12


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")


def own_text(message: Message) -> str:
    text = message.text if message.text is not None else message.caption or ""
    entities = message.entities if message.text is not None else message.caption_entities
    encoded = bytearray(text.encode("utf-16-le"))
    for entity in entities or ():
        if entity.type in {
            MessageEntityType.BLOCKQUOTE, MessageEntityType.EXPANDABLE_BLOCKQUOTE,
            MessageEntityType.CODE, MessageEntityType.PRE,
        }:
            start, end = entity.offset * 2, (entity.offset + entity.length) * 2
            encoded[start:end] = b" \x00" * entity.length
    return normalize(QUOTES.sub(" ", encoded.decode("utf-16-le")))


def is_direct_insult(
    text: str, *, addressed: bool = False, recent_exchange: bool = False,
    storytelling: bool = False,
) -> bool:
    text = normalize(text)
    if not INSULT.search(text) or REPORT.search(text) or STORY.search(text):
        return False
    if storytelling and not addressed:
        return False
    for clause in CLAUSE_SEPARATOR.split(text):
        clause = NEGATED_INSULT.sub(" ", clause)
        matches = list(INSULT.finditer(clause))
        for match in reversed(matches):
            if OTHER_SUBJECT.search(clause[:match.start()]):
                clause = clause[:match.start()] + " " + clause[match.end():]
        matches = list(INSULT.finditer(clause))
        if not matches:
            continue
        if addressed or ADDRESS.search(clause) or IMPERATIVE.search(clause):
            return True
        remainder = INSULT.sub(" ", clause)
        if recent_exchange and not WORD.search(remainder):
            return True
    return False


class InsultFilter(Filter):
    def __init__(self) -> None:
        self.history: OrderedDict[tuple[int, int | None], deque] = OrderedDict()

    async def __call__(self, message: Message) -> bool | dict[str, str]:
        author = message.from_user
        if (message.chat.type != ChatType.SUPERGROUP or not author
                or author.is_bot or message.sender_chat):
            return False
        text = own_text(message)
        if not text.strip():
            return False
        now = time.monotonic()
        key = (message.chat.id, message.message_thread_id)
        history = self.history.setdefault(key, deque(maxlen=MAX_MESSAGES))
        self.history.move_to_end(key)
        while len(self.history) > MAX_CHATS:
            self.history.popitem(last=False)
        while history and now - history[0][0] > HISTORY_SECONDS:
            history.popleft()
        previous = [entry for entry in history if entry[1] != message.message_id]
        storytelling = any(
            entry[2] == author.id and (REPORT.search(entry[3]) or STORY.search(entry[3]))
            for entry in previous
        )
        recent_exchange = bool(previous and previous[-1][2] != author.id)
        reply = message.reply_to_message
        addressed = bool(
            reply and reply.from_user and not reply.sender_chat
            and not reply.from_user.is_bot and reply.from_user.id != author.id
        )
        entities = message.entities if message.text is not None else message.caption_entities
        source = message.text if message.text is not None else message.caption or ""
        for entity in entities or ():
            if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
                mention = normalize(entity.extract_from(source))
                addressed |= (mention in text and entity.user.id != author.id
                              and not entity.user.is_bot)
            elif entity.type == MessageEntityType.MENTION:
                mention = normalize(entity.extract_from(source))
                addressed |= mention in text and mention != "@" + normalize(author.username or "")
        if message.edit_date is None:
            history.append((now, message.message_id, author.id, text[:2000]))
        if is_direct_insult(
            text, addressed=addressed, recent_exchange=recent_exchange,
            storytelling=storytelling,
        ):
            return {"moderation_reason": "оскорбление"}
        return False
