import time

from aiogram import BaseMiddleware
from aiogram.types import Message


class OldMessagesMiddleware(BaseMiddleware):
    def __init__(self, started_at: int) -> None:
        self.started_at = started_at
        self.last_poll_at = time.time()

    def discard_before(self, timestamp: int) -> None:
        self.started_at = max(self.started_at, timestamp)

    def poll_received(self, timestamp: float) -> None:
        self.last_poll_at = timestamp

    def is_current(self, event: Message) -> bool:
        now = time.time()
        if now - self.last_poll_at > 30:
            # The process may have slept after receiving a batch, before its
            # handlers ran. Those already-buffered messages are stale as well.
            self.discard_before(int(now) + 1)
            self.last_poll_at = now
        timestamp = event.edit_date if event.edit_date is not None else event.date.timestamp()
        return timestamp >= self.started_at

    async def __call__(self, handler, event: Message, data: dict):
        if self.is_current(event):
            data['message_gate'] = self
            return await handler(event, data)
        return None
