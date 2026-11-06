import argparse
import asyncio
import json
from pathlib import Path

from aiogram.utils.token import extract_bot_id

from src.config import PROJECT_ROOT, get_bot_token

from src.handlers.mem.meme_repository import MemeRepository
from src.handlers.mem.photo_filter import export_is_channel_forward


async def import_export(
    path: Path, store: MemeRepository, chat_id: int | None = None, *, bot_id: int, replace: bool = False,
) -> dict[str, int]:
    if type(bot_id) is not int or bot_id <= 0:
        raise ValueError("Укажите положительный ID бота для исключения его фотографий")

    def read_ids() -> tuple[int, list[int], list[int], dict[str, int]]:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('type') not in {'private_supergroup', 'public_supergroup'}:
            raise ValueError("Ожидается экспорт супергруппы")
        if type(data.get('id')) is not int or data['id'] <= 0:
            raise ValueError("Некорректный ID группы в экспорте")
        source_chat_id = -(1_000_000_000_000 + data['id'])
        if chat_id is not None and source_chat_id != chat_id:
            raise ValueError("Экспорт относится к другой группе")
        photos = [m for m in data['messages'] if m.get('type') == 'message' and 'photo' in m]
        ids = [m['id'] for m in photos]
        if not all(type(mid) is int and mid != 0 for mid in ids):
            raise ValueError("Некорректный ID фотографии в экспорте")
        skipped = {"skipped_negative": 0, "skipped_channels": 0, "skipped_bots": 0}
        allowed_ids = []
        excluded_ids = []
        for photo in photos:
            if photo['id'] < 0:
                skipped['skipped_negative'] += 1
                continue
            if photo.get('from_id') == f'user{bot_id}':
                skipped['skipped_bots'] += 1
                excluded_ids.append(photo['id'])
            elif export_is_channel_forward(photo):
                skipped['skipped_channels'] += 1
                excluded_ids.append(photo['id'])
            else:
                allowed_ids.append(photo['id'])
        return source_chat_id, allowed_ids, excluded_ids, skipped

    source_chat_id, ids, excluded_ids, skipped = await asyncio.to_thread(read_ids)
    await store.initialize()
    result = await store.import_ids(source_chat_id, ids, excluded_ids=excluded_ids, replace=replace)
    return {**result, **skipped}


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт ID фотографий из Telegram Desktop JSON без скачивания медиа")
    parser.add_argument('export', type=Path)
    parser.add_argument('--replace', action='store_true', help="Пересобрать каталог фотографий только этой беседы")
    parser.add_argument('--bot-id', type=int, help="ID бота; по умолчанию берётся из BOT_TOKEN без обращения к Telegram")
    args = parser.parse_args()
    bot_id = args.bot_id if args.bot_id is not None else extract_bot_id(get_bot_token())
    result = asyncio.run(import_export(
        args.export, MemeRepository(PROJECT_ROOT / 'data' / 'memes.sqlite3'), bot_id=bot_id, replace=args.replace,
    ))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
