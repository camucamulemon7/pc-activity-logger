from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


EXPORT_FOLDER = "PC Activity Logger"


def _escape_text(value: str) -> str:
    value = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|~\-])", r"\\\1", value)


def _inline_text(value: str) -> str:
    return _escape_text(" ".join(value.split()))


def _canonical(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def _safe_name(value: str, *, tag: bool = False) -> str:
    """Keep simple terms readable; disambiguate sanitized names with a hash."""
    canonical = _canonical(value)
    pattern = r"[^\w-]" if tag else r"[^\w.\-]"
    name = re.sub(pattern, "-", canonical).strip(".-_")[:80]
    reserved = name.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not name or name != canonical or reserved:
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
        name = f"{name or 'term'}-{digest}"
    return name


def _timestamp(record: dict[str, Any]) -> datetime:
    value = record.get("timestamp")
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    try:
        # Python 3.10 does not accept the UTC 'Z' suffix in fromisoformat.
        return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("timestamp must be an ISO 8601 string") from exc


def _validate_record(record: dict[str, Any]) -> None:
    _timestamp(record)
    for field in ("activity", "detail"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
    for field in ("project", "category", "app_name", "window_title",
                  "screenshot", "monitor_screenshot"):
        if field in record and not isinstance(record[field], str):
            raise ValueError(f"{field} must be a string")
    confidence = record.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("confidence must be a finite number between 0 and 1")
    keywords = record.get("keywords", [])
    if (not isinstance(keywords, list) or len(keywords) > 8
            or any(not isinstance(word, str) or not word.strip() or len(word) > 64
                   for word in keywords)):
        raise ValueError("keywords must be a list of at most 8 non-empty strings (64 characters each)")


def _entities(record: dict[str, Any]) -> list[tuple[str, str]]:
    entities = [("Days", _timestamp(record).date().isoformat())]
    for folder, field in (("Projects", "project"), ("Apps", "app_name"),
                          ("Categories", "category")):
        value = record.get(field, "").strip()
        if value and _canonical(value) not in {"unknown", "不明"}:
            entities.append((folder, value))
    seen: set[str] = set()
    for word in record.get("keywords", []):
        if _canonical(word) not in seen:
            seen.add(_canonical(word))
            entities.append(("Topics", word))
    return entities


def _entity_path(folder: str, value: str) -> Path:
    return Path(EXPORT_FOLDER) / folder / f"{_safe_name(value)}.md"


def _wikilink(folder: str, value: str) -> str:
    label = re.sub(r"[\[\]|#^\r\n]", " ", value)
    label = html.escape(" ".join(label.split()), quote=False)
    return f"[[{_entity_path(folder, value).as_posix()}|{label}]]"


def _record_path(record: dict[str, Any]) -> Path:
    captured_at = _timestamp(record)
    identity = {key: record.get(key) for key in (
        "timestamp", "app_name", "window_title", "screenshot", "activity",
    )}
    identity_json = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:12]
    name = captured_at.strftime("%Y%m%d-%H%M%S-%f") + f"-{digest}.md"
    return Path(EXPORT_FOLDER) / "Records" / captured_at.date().isoformat() / name


def _frontmatter(properties: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(properties, allow_unicode=True, sort_keys=False) + "---\n\n"


def format_activity_markdown(record: dict[str, Any]) -> str:
    """Create one Obsidian note with properties, tags, and actual note links."""
    _validate_record(record)
    entities = _entities(record)
    tags = ["pc-activity"]
    for folder, value in entities:
        prefix = {"Projects": "project", "Apps": "app", "Categories": "category",
                  "Topics": "keyword"}.get(folder)
        if prefix:
            tags.append(f"{prefix}/{_safe_name(value, tag=True)}")
    properties = {
        "type": "pc-activity-record",
        "timestamp": record["timestamp"],
        "date": _timestamp(record).date().isoformat(),
        "activity": " ".join(record["activity"].split()),
        "app": record.get("app_name", ""),
        "project": record.get("project", ""),
        "category": record.get("category", ""),
        "confidence": record["confidence"],
        "keywords": record.get("keywords", []),
        "tags": tags,
        "related": [_wikilink(folder, value) for folder, value in entities],
    }
    lines = [f"# {_inline_text(record['activity'])}", "",
             f"- **記録時刻:** {_timestamp(record).isoformat(sep=' ')}",
             f"- **ウィンドウ:** {_inline_text(record.get('window_title', '—'))}",
             f"- **信頼度:** {record['confidence']:.2f}", "", "## 作業内容", ""]
    lines.extend(_escape_text(line.strip()) for line in record["detail"].splitlines())
    lines.extend(["", "## 関連ノート", ""])
    lines.extend(f"- {_wikilink(folder, value)}" for folder, value in entities)
    if record.get("screenshot") or record.get("monitor_screenshot"):
        lines.extend(["", "## 画像の保存場所", ""])
        for label, field in (("前面ウィンドウ", "screenshot"), ("モニター", "monitor_screenshot")):
            if record.get(field):
                lines.append(f"- **{label}:** {_inline_text(record[field])}")
    return _frontmatter(properties) + "\n".join(lines) + "\n"


def _write_new_note(path: Path, text: str, vault: Path) -> None:
    # Exporting the same history again preserves annotations in existing notes.
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError("Markdown note path must stay inside the Obsidian vault")
    if path.exists():
        if not path.is_file():
            raise ValueError(f"Markdown note path is not a file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix=f".{path.name}.",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as output:
            temporary_path = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        if not path.exists():
            os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_activity_markdown(record: dict[str, Any], vault: Path) -> Path:
    content = format_activity_markdown(record)
    for folder, value in _entities(record):
        hub = _frontmatter({"type": "pc-activity-topic", "aliases": [value],
                            "tags": ["pc-activity/topic"]})
        hub += f"# {_inline_text(value)}\n\n関連する作業記録はObsidianのバックリンクから確認できます。\n"
        _write_new_note(vault / _entity_path(folder, value), hub, vault)
    path = vault / _record_path(record)
    _write_new_note(path, content, vault)
    return path


def export_markdown(source: Path, vault: Path) -> int:
    """Export a JSONL file or history directory into an Obsidian vault."""
    source, vault = Path(source), Path(vault)
    if not source.exists():
        raise FileNotFoundError(f"History not found: {source}")
    if vault.exists() and not vault.is_dir():
        raise ValueError("Obsidian output must be a directory")
    paths = sorted(source.rglob("activity.jsonl")) if source.is_dir() else [source]
    if not paths:
        raise ValueError(f"No activity.jsonl history found in {source}")
    records: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8-sig") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("record must be a JSON object")
                    _validate_record(record)
                except (ValueError, OverflowError) as exc:
                    raise ValueError(
                        f"{path}: line {line_number}: invalid activity record ({exc})"
                    ) from exc
                records.append(record)
    for record in records:
        write_activity_markdown(record, vault)
    return len(records)
