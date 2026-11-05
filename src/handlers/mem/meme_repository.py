import asyncio
import random
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class MemeRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS photos (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    file_id TEXT,
                    file_unique_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    added_at INTEGER NOT NULL,
                    last_sent_at REAL,
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS photos_status ON photos(chat_id, status);
                CREATE TABLE IF NOT EXISTS daily_posts (
                    chat_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    scheduled_at INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'planned',
                    sent_at INTEGER,
                    sent_message_id INTEGER,
                    PRIMARY KEY (chat_id, day)
                );
            """)

    async def import_ids(self, chat_id: int, message_ids: list[int]) -> dict[str, int]:
        return await asyncio.to_thread(self._import_ids, chat_id, message_ids)

    def _import_ids(self, chat_id: int, message_ids: list[int]) -> dict[str, int]:
        if not all(type(mid) is int and mid > 0 for mid in message_ids):
            raise ValueError("В каталог можно импортировать только положительные ID сообщений")
        with self.connection() as db:
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO photos(chat_id, message_id, status, added_at) VALUES (?, ?, ?, ?)",
                [(chat_id, mid, 'active', int(time.time())) for mid in message_ids],
            )
            inserted = db.total_changes - before
            counts = dict(db.execute(
                "SELECT status, COUNT(*) FROM photos WHERE chat_id=? GROUP BY status", (chat_id,),
            ).fetchall())
            return {"inserted": inserted, **counts}

    async def add_photo(self, chat_id: int, message_id: int, file_id: str, unique_id: str) -> None:
        await asyncio.to_thread(self._add_photo, chat_id, message_id, file_id, unique_id)

    def _add_photo(self, chat_id: int, message_id: int, file_id: str, unique_id: str) -> None:
        with self.connection() as db:
            existing = db.execute(
                "SELECT message_id FROM photos WHERE chat_id=? AND message_id=?", (chat_id, message_id),
            ).fetchone()
            if not existing and db.execute(
                "SELECT 1 FROM photos WHERE chat_id=? AND file_unique_id=? AND status='active'",
                (chat_id, unique_id),
            ).fetchone():
                return
            db.execute("""
                INSERT INTO photos(chat_id, message_id, file_id, file_unique_id, added_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    file_id=excluded.file_id, file_unique_id=excluded.file_unique_id, status='active'
            """, (chat_id, message_id, file_id, unique_id, int(time.time())))

    async def choose(self, chat_id: int) -> dict | None:
        return await asyncio.to_thread(self._choose, chat_id)

    def _choose(self, chat_id: int) -> dict | None:
        with self.connection() as db:
            photos = db.execute(
                "SELECT * FROM photos WHERE chat_id=? AND status='active'", (chat_id,),
            ).fetchall()
            if not photos:
                return None
            last = max(photos, key=lambda photo: photo['last_sent_at'] or 0)
            candidates = [p for p in photos if p['message_id'] != last['message_id']] if last['last_sent_at'] and len(photos) > 1 else photos
            return dict(random.choice(candidates))

    async def update_photo(self, chat_id: int, message_id: int, *, unavailable: bool = False) -> None:
        await asyncio.to_thread(self._update_photo, chat_id, message_id, unavailable)

    def _update_photo(self, chat_id: int, message_id: int, unavailable: bool) -> None:
        with self.connection() as db:
            if unavailable:
                db.execute("UPDATE photos SET status='unavailable' WHERE chat_id=? AND message_id=?", (chat_id, message_id))
            else:
                db.execute("UPDATE photos SET last_sent_at=? WHERE chat_id=? AND message_id=?", (time.time(), chat_id, message_id))

    async def latest_post_day(self, chat_id: int, before_day: str) -> str | None:
        return await asyncio.to_thread(self._latest_post_day, chat_id, before_day)

    def _latest_post_day(self, chat_id: int, before_day: str) -> str | None:
        with self.connection() as db:
            row = db.execute("""
                SELECT day FROM daily_posts
                WHERE chat_id=? AND day<? AND state IN ('sent', 'sending', 'uncertain')
                ORDER BY day DESC LIMIT 1
            """, (chat_id, before_day)).fetchone()
            return row['day'] if row else None

    async def daily_plan(self, chat_id: int, day: str, scheduled_at: int) -> dict:
        return await asyncio.to_thread(self._daily_plan, chat_id, day, scheduled_at)

    def _daily_plan(self, chat_id: int, day: str, scheduled_at: int) -> dict:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO daily_posts(chat_id, day, scheduled_at) VALUES (?, ?, ?)", (chat_id, day, scheduled_at))
            return dict(db.execute("SELECT * FROM daily_posts WHERE chat_id=? AND day=?", (chat_id, day)).fetchone())

    async def claim_daily(self, chat_id: int, day: str) -> bool:
        return await asyncio.to_thread(self._claim_daily, chat_id, day)

    def _claim_daily(self, chat_id: int, day: str) -> bool:
        with self.connection() as db:
            return db.execute(
                "UPDATE daily_posts SET state='sending' WHERE chat_id=? AND day=? AND state='planned'",
                (chat_id, day),
            ).rowcount == 1

    async def finish_daily(self, chat_id: int, day: str, state: str, scheduled_at: int, sent_message_id: int | None = None) -> None:
        await asyncio.to_thread(self._finish_daily, chat_id, day, state, scheduled_at, sent_message_id)

    def _finish_daily(self, chat_id: int, day: str, state: str, scheduled_at: int, sent_message_id: int | None) -> None:
        with self.connection() as db:
            db.execute("""
                UPDATE daily_posts SET state=?, scheduled_at=?, sent_at=?, sent_message_id=?
                WHERE chat_id=? AND day=?
            """, (state, scheduled_at, int(time.time()) if state == 'sent' else None, sent_message_id, chat_id, day))
