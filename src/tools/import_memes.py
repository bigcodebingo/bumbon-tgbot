import argparse
import asyncio
import json
from pathlib import Path

from src.config import PROJECT_ROOT

from src.handlers.mem.meme_repository import MemeRepository


async def import_export(path: Path, store: MemeRepository, chat_id: int | None = None) -> dict[str, int]:
    def read_ids() -> tuple[int, list[int], int]:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('type') not in {'private_supergroup', 'public_supergroup'}:
            raise ValueError("Ожидается экспорт супергруппы")
        if type(data.get('id')) is not int or data['id'] <= 0:
            raise ValueError("Некорректный ID группы в экспорте")
        source_chat_id = -(1_000_000_000_000 + data['id'])
        if chat_id is not None and source_chat_id != chat_id:
            raise ValueError("Экспорт относится к другой группе")
        ids = [m['id'] for m in data['messages'] if m.get('type') == 'message' and 'photo' in m]
        if not all(type(mid) is int and mid != 0 for mid in ids):
            raise ValueError("Некорректный ID фотографии в экспорте")
        skipped_negative = sum(mid < 0 for mid in ids)
        return source_chat_id, [mid for mid in ids if mid > 0], skipped_negative

    source_chat_id, ids, skipped_negative = await asyncio.to_thread(read_ids)
    await store.initialize()
    result = await store.import_ids(source_chat_id, ids)
    return {**result, "skipped_negative": skipped_negative}


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт ID фотографий из Telegram Desktop JSON без скачивания медиа")
    parser.add_argument('export', type=Path)
    args = parser.parse_args()
    result = asyncio.run(import_export(args.export, MemeRepository(PROJECT_ROOT / 'data' / 'memes.sqlite3')))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
