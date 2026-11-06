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
                    sent_in_cycle INTEGER NOT NULL DEFAULT 0,
                    in_current_cycle INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS photos_status ON photos(chat_id, status);
                DROP TABLE IF EXISTS daily_posts;
            """)
            columns = {row['name'] for row in db.execute("PRAGMA table_info(photos)")}
            if 'sent_in_cycle' not in columns:
                db.execute("ALTER TABLE photos ADD COLUMN sent_in_cycle INTEGER NOT NULL DEFAULT 0")
            if 'in_current_cycle' not in columns:
                db.execute("ALTER TABLE photos ADD COLUMN in_current_cycle INTEGER NOT NULL DEFAULT 0")
                # Freeze an already-started cycle while retaining its sent marks.
                db.execute("""
                    UPDATE photos SET in_current_cycle=1
                    WHERE status='active' AND chat_id IN (
                        SELECT chat_id FROM photos WHERE sent_in_cycle=1
                    )
                """)

    async def import_ids(
        self, chat_id: int, message_ids: list[int], *, excluded_ids: list[int] | None = None,
        replace: bool = False,
    ) -> dict[str, int]:
        return await asyncio.to_thread(self._import_ids, chat_id, message_ids, excluded_ids or [], replace)

    def _import_ids(
        self, chat_id: int, message_ids: list[int], excluded_ids: list[int], replace: bool,
    ) -> dict[str, int]:
        if not all(type(mid) is int and mid > 0 for mid in message_ids + excluded_ids):
            raise ValueError("В каталог можно импортировать только положительные ID сообщений")
        if set(message_ids) & set(excluded_ids):
            raise ValueError("ID фотографии не может одновременно импортироваться и исключаться")
        with self.connection() as db:
            before = db.total_changes
            if replace:
                db.execute("DELETE FROM photos WHERE chat_id=?", (chat_id,))
            else:
                db.executemany(
                    "DELETE FROM photos WHERE chat_id=? AND message_id=?",
                    [(chat_id, mid) for mid in excluded_ids],
                )
            removed = db.total_changes - before
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO photos(chat_id, message_id, status, added_at) VALUES (?, ?, ?, ?)",
                [(chat_id, mid, 'active', int(time.time())) for mid in message_ids],
            )
            inserted = db.total_changes - before
            counts = dict(db.execute(
                "SELECT status, COUNT(*) FROM photos WHERE chat_id=? GROUP BY status", (chat_id,),
            ).fetchall())
            return {"inserted": inserted, "removed": removed, **counts}

    async def remove_photo(self, chat_id: int, message_id: int) -> None:
        await asyncio.to_thread(self._remove_photo, chat_id, message_id)

    def _remove_photo(self, chat_id: int, message_id: int) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM photos WHERE chat_id=? AND message_id=?", (chat_id, message_id))

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
                "SELECT * FROM photos WHERE chat_id=? AND status='active' "
                "AND in_current_cycle=1 AND sent_in_cycle=0", (chat_id,),
            ).fetchall()
            if not photos:
                # Snapshot the next cycle. Later arrivals keep the default 0
                # membership and cannot prolong the cycle currently in progress.
                db.execute("UPDATE photos SET in_current_cycle=0 WHERE chat_id=?", (chat_id,))
                db.execute(
                    "UPDATE photos SET in_current_cycle=1, sent_in_cycle=0 "
                    "WHERE chat_id=? AND status='active'", (chat_id,),
                )
                photos = db.execute(
                    "SELECT * FROM photos WHERE chat_id=? AND status='active'", (chat_id,),
                ).fetchall()
                if not photos:
                    return None
            last = db.execute(
                "SELECT message_id FROM photos WHERE chat_id=? AND status='active' "
                "AND last_sent_at IS NOT NULL ORDER BY last_sent_at DESC LIMIT 1", (chat_id,),
            ).fetchone()
            # Also avoid an immediate repeat across the boundary of two cycles.
            if last is not None and len(photos) > 1:
                photos = [photo for photo in photos if photo['message_id'] != last['message_id']]
            return dict(random.choice(photos))

    async def update_photo(self, chat_id: int, message_id: int, *, unavailable: bool = False) -> None:
        await asyncio.to_thread(self._update_photo, chat_id, message_id, unavailable)

    def _update_photo(self, chat_id: int, message_id: int, unavailable: bool) -> None:
        with self.connection() as db:
            if unavailable:
                db.execute("UPDATE photos SET status='unavailable' WHERE chat_id=? AND message_id=?", (chat_id, message_id))
            else:
                db.execute(
                    "UPDATE photos SET last_sent_at=?, sent_in_cycle=1 WHERE chat_id=? AND message_id=?",
                    (time.time(), chat_id, message_id),
                )
