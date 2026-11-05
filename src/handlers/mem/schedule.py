import asyncio
import logging
import random
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter

from .meme_service import MemeService

logger = logging.getLogger(__name__)
MOSCOW = timezone(timedelta(hours=3), "MSK")
POST_INTERVAL_DAYS = 3


def quiet_hours(moment: datetime) -> bool:
    return 3 <= moment.astimezone(MOSCOW).hour < 8


def next_allowed(moment: datetime) -> datetime:
    moment = moment.astimezone(MOSCOW)
    return moment.replace(hour=8, minute=0, second=0, microsecond=0) if quiet_hours(moment) else moment


def random_daily_time(now: datetime) -> datetime:
    now = now.astimezone(MOSCOW)
    midnight = datetime.combine(now.date(), time(), MOSCOW)
    intervals = ((midnight, midnight + timedelta(hours=3)), (midnight + timedelta(hours=8), midnight + timedelta(days=1)))
    remaining = []
    for start, end in intervals:
        start = max(start, now.replace(microsecond=0) + timedelta(seconds=1))
        seconds = int((end - start).total_seconds())
        if seconds > 0:
            remaining.append((start, seconds))
    total = sum(seconds for _, seconds in remaining)
    if not total:
        return midnight + timedelta(days=1)
    offset = random.randrange(total)
    for start, seconds in remaining:
        if offset < seconds:
            return start + timedelta(seconds=offset)
        offset -= seconds
    raise AssertionError("Random schedule interval not found")


class DailyScheduler:
    def __init__(self, service: MemeService) -> None:
        self.service = service
        self.task: asyncio.Task | None = None
        self.first_tick = True

    async def start(self, bot: Bot) -> None:
        self.task = asyncio.create_task(self.run(bot))

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def tick(self, bot: Bot, now: datetime | None = None) -> None:
        current_time = (lambda: now.astimezone(MOSCOW)) if now is not None else (lambda: datetime.now(MOSCOW))
        now = current_time()
        store, chat_id = self.service.store, self.service.chat_id
        day = now.date().isoformat()
        last_post_day = await store.latest_post_day(chat_id, day)
        if last_post_day is not None:
            next_post_day = date.fromisoformat(last_post_day) + timedelta(days=POST_INTERVAL_DAYS)
            if now.date() < next_post_day:
                return
        plan = await store.daily_plan(chat_id, day, int(random_daily_time(now).timestamp()))
        first_tick, self.first_tick = self.first_tick, False
        if plan['state'] == 'sending':
            # After a crash the request may already have reached Telegram.
            # Do not duplicate a publication whose delivery cannot be confirmed.
            await store.finish_daily(chat_id, day, 'uncertain', plan['scheduled_at'])
            logger.warning("Daily photo delivery is uncertain for %s; no automatic repeat", day)
            return
        if plan['state'] != 'planned':
            return
        if plan['scheduled_at'] < now.timestamp() and (first_tick or quiet_hours(now)):
            await store.finish_daily(chat_id, day, 'planned', int(random_daily_time(now).timestamp()))
            return
        if quiet_hours(now) or now.timestamp() < plan['scheduled_at']:
            return
        if not await store.claim_daily(chat_id, day):
            return

        def can_send() -> bool:
            current = current_time()
            return current.date().isoformat() == day and not quiet_hours(current)

        try:
            result, message_id = await self.service.send_random(bot, can_send=can_send)
        except (TelegramNetworkError, asyncio.TimeoutError):
            await store.finish_daily(chat_id, day, 'uncertain', plan['scheduled_at'])
            logger.exception("Daily photo request may have been delivered; no automatic repeat")
            return
        except TelegramAPIError as error:
            delay = max(300, error.retry_after) if isinstance(error, TelegramRetryAfter) else 300
            retry = next_allowed(now + timedelta(seconds=delay))
            await store.finish_daily(chat_id, day, 'planned', int(retry.timestamp()))
            logger.warning("Daily photo rejected: %s", error.message)
            return
        if result == 'sent':
            await store.finish_daily(chat_id, day, 'sent', plan['scheduled_at'], message_id)
        else:
            retry = next_allowed(now + timedelta(minutes=5))
            await store.finish_daily(chat_id, day, 'planned', int(retry.timestamp()))

    async def run(self, bot: Bot) -> None:
        while True:
            try:
                await self.tick(bot)
            except Exception:
                logger.exception("Daily photo scheduler failed")
            await asyncio.sleep(1)
