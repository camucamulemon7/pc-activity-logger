from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def day_directory(data_dir: Path, captured_at: datetime) -> Path:
    path = data_dir / captured_at.astimezone().date().isoformat()
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_screenshot(
    data_dir: Path,
    captured_at: datetime,
    image_bytes: bytes,
    kind: str = "active",
) -> Path:
    if not kind.isascii() or not kind.replace("_", "").isalnum():
        raise ValueError("Screenshot kind must contain only ASCII letters, numbers, or _")
    screenshots = day_directory(data_dir, captured_at) / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    name = captured_at.astimezone().strftime("%H%M%S_%f") + f"_{kind}.jpg"
    path = screenshots / name
    temp = path.with_suffix(".jpg.tmp")
    temp.write_bytes(image_bytes)
    os.replace(temp, path)
    return path


def append_activity(data_dir: Path, captured_at: datetime, record: dict[str, Any]) -> Path:
    path = day_directory(data_dir, captured_at) / "activity.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return path
