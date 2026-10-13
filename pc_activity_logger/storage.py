from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("pc_activity_logger")
SCREENSHOT_NAME = re.compile(r"\d{6}_\d{6}_(?:active|monitor)\.jpg\Z")


def cleanup_screenshots(data_dir: Path, retention_days: int, now: datetime | None = None) -> int:
    """Keep the current day plus retention_days - 1 days; never follow links."""
    if retention_days <= 0 or not data_dir.exists():
        return 0
    root = data_dir.resolve()
    cutoff = (now or datetime.now().astimezone()).astimezone().date() - timedelta(days=retention_days - 1)
    removed = 0
    for day in root.iterdir():
        try:
            day_date = date.fromisoformat(day.name)
        except ValueError:
            continue
        if (day_date.isoformat() != day.name or day_date >= cutoff or not day.is_dir()
                or day.is_symlink() or day.resolve() != root / day.name):
            continue
        screenshots = day / "screenshots"
        if (not screenshots.is_dir() or screenshots.is_symlink()
                or screenshots.resolve() != day / "screenshots"):
            continue
        for image in screenshots.iterdir():
            if (not SCREENSHOT_NAME.fullmatch(image.name) or image.is_symlink()
                    or not image.is_file() or image.resolve().parent != screenshots):
                continue
            try:
                image.unlink()
                removed += 1
            except OSError:
                LOGGER.warning("Could not remove expired screenshot: %s", image, exc_info=True)
    return removed


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
