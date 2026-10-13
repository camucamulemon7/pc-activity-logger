from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("pc_activity_logger")


def record_timestamp(record: dict[str, Any]) -> datetime:
    value = record.get("timestamp")
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO 8601 string") from exc


def validate_record(record: dict[str, Any]) -> None:
    record_timestamp(record)
    for field in ("activity", "detail"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    for field in ("project", "category", "app_name", "window_title", "screenshot", "monitor_screenshot"):
        if field in record and not isinstance(record[field], str):
            raise ValueError(f"{field} must be a string")
    confidence = record.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("confidence must be a finite number between 0 and 1")
    keywords = record.get("keywords", [])
    if (not isinstance(keywords, list) or len(keywords) > 8
            or any(not isinstance(word, str) or not word.strip() or len(word) > 64 for word in keywords)):
        raise ValueError("keywords must be a list of at most 8 non-empty strings (64 characters each)")


@dataclass
class HistoryRecords:
    records: list[dict[str, Any]]
    skipped: int = 0


def read_history(source: Path, *, strict: bool = False) -> HistoryRecords:
    source = Path(source)
    if not source.exists():
        raise FileNotFoundError(f"History not found: {source}")
    paths = sorted(source.rglob("activity.jsonl")) if source.is_dir() else [source]
    if not paths:
        raise ValueError(f"No activity.jsonl history found in {source}")
    result = HistoryRecords([])
    for path in paths:
        with path.open("rb") as input_file:
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                try:
                    line = raw_line.decode("utf-8-sig" if line_number == 1 else "utf-8")
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("record must be a JSON object")
                    validate_record(record)
                except (ValueError, OverflowError) as exc:
                    reason = "invalid UTF-8" if isinstance(exc, UnicodeDecodeError) else str(exc)
                    message = f"{path}: line {line_number}: invalid activity record ({reason})"
                    if strict:
                        raise ValueError(message) from exc
                    LOGGER.warning("Skipping %s", message)
                    result.skipped += 1
                    continue
                result.records.append(record)
    return result
