import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_bot_token() -> str:
    env_file = PROJECT_ROOT / ".env"
    load_dotenv(env_file, encoding="utf-8-sig")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("укажите BOT_TOKEN в .env или переменных окружения")

    return token

