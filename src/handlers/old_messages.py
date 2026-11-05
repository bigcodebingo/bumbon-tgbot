from aiogram import BaseMiddleware
from aiogram.types import Message


class OldMessagesMiddleware(BaseMiddleware):
    def __init__(self, started_at: int) -> None:
        self.started_at = started_at

    async def __call__(self, handler, event: Message, data: dict):
        timestamp = event.edit_date if event.edit_date is not None else event.date.timestamp()
        if timestamp >= self.started_at:
            return await handler(event, data)
        return None
