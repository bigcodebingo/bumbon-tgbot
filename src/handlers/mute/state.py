import asyncio
import json
import os
from pathlib import Path


async def read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid state in {path.name}: expected an object")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


async def write_state(path: Path, data: dict) -> None:
    await asyncio.to_thread(_write, path, data)
