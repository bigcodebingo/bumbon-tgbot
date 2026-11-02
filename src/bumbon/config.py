import os
from pathlib import Path

from dotenv import load_dotenv


def get_bot_token() -> str:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_file, encoding="utf-8-sig")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("Укажите BOT_TOKEN в .env или переменных окружения.")

    return token

