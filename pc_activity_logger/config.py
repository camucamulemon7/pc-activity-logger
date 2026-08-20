from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class OpenWebUIConfig:
    base_url: str
    api_key: str
    model: str
    timeout_sec: int = 120
    max_tokens: int = 1024


@dataclass(frozen=True)
class CaptureConfig:
    interval_sec: int = 180
    jpeg_quality: int = 80
    idle_threshold_sec: int = 300
    skip_same_screen: bool = True
    same_screen_max_distance: int = 3
    same_screen_force_after_sec: int = 900
    skip_unavailable_session: bool = True


@dataclass(frozen=True)
class StorageConfig:
    data_dir: Path = Path("data")


@dataclass(frozen=True)
class NotesConfig:
    enabled: bool = False
    title_prefix: str = "PC作業記録"


@dataclass(frozen=True)
class Config:
    openwebui: OpenWebUIConfig
    capture: CaptureConfig
    storage: StorageConfig
    notes: NotesConfig


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"config.yaml: '{name}' section is required")
    return value


def load_config(path: Path) -> Config:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("config.yaml must contain a YAML mapping")

    ow = _section(raw, "openwebui")
    cap = raw.get("capture", {})
    storage = raw.get("storage", {})
    notes = raw.get("notes", {})
    if not isinstance(cap, dict) or not isinstance(storage, dict) or not isinstance(notes, dict):
        raise ValueError("'capture', 'storage', and 'notes' must be YAML mappings")

    for key in ("base_url", "api_key", "model"):
        if not isinstance(ow.get(key), str) or not ow[key].strip():
            raise ValueError(f"openwebui.{key} must be a non-empty string")
    if ow["api_key"] == "YOUR_API_KEY":
        raise ValueError("Replace openwebui.api_key in config.yaml")

    interval = int(cap.get("interval_sec", 180))
    quality = int(cap.get("jpeg_quality", 80))
    idle_threshold = int(cap.get("idle_threshold_sec", 300))
    skip_same_screen = cap.get("skip_same_screen", True)
    same_screen_max_distance = int(cap.get("same_screen_max_distance", 3))
    same_screen_force_after = int(cap.get("same_screen_force_after_sec", 900))
    skip_unavailable_session = cap.get("skip_unavailable_session", True)
    timeout = int(ow.get("timeout_sec", 120))
    max_tokens = int(ow.get("max_tokens", 1024))
    if interval < 1:
        raise ValueError("capture.interval_sec must be at least 1")
    if not 1 <= quality <= 95:
        raise ValueError("capture.jpeg_quality must be between 1 and 95")
    if idle_threshold < 0:
        raise ValueError("capture.idle_threshold_sec must be zero or greater")
    if not isinstance(skip_same_screen, bool):
        raise ValueError("capture.skip_same_screen must be true or false")
    if not 0 <= same_screen_max_distance <= 64:
        raise ValueError("capture.same_screen_max_distance must be between 0 and 64")
    if same_screen_force_after < 1:
        raise ValueError("capture.same_screen_force_after_sec must be at least 1")
    if not isinstance(skip_unavailable_session, bool):
        raise ValueError("capture.skip_unavailable_session must be true or false")
    if timeout < 1:
        raise ValueError("openwebui.timeout_sec must be at least 1")
    if max_tokens < 64:
        raise ValueError("openwebui.max_tokens must be at least 64")

    config_dir = path.resolve().parent
    data_dir = Path(storage.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = config_dir / data_dir
    notes_enabled = notes.get("enabled", False)
    if not isinstance(notes_enabled, bool):
        raise ValueError("notes.enabled must be true or false")
    title_prefix = notes.get("title_prefix", "PC作業記録")
    if not isinstance(title_prefix, str) or not title_prefix.strip():
        raise ValueError("notes.title_prefix must be a non-empty string")

    return Config(
        openwebui=OpenWebUIConfig(
            base_url=ow["base_url"].rstrip("/"),
            api_key=ow["api_key"],
            model=ow["model"],
            timeout_sec=timeout,
            max_tokens=max_tokens,
        ),
        capture=CaptureConfig(
            interval_sec=interval,
            jpeg_quality=quality,
            idle_threshold_sec=idle_threshold,
            skip_same_screen=skip_same_screen,
            same_screen_max_distance=same_screen_max_distance,
            same_screen_force_after_sec=same_screen_force_after,
            skip_unavailable_session=skip_unavailable_session,
        ),
        storage=StorageConfig(data_dir=data_dir),
        notes=NotesConfig(enabled=notes_enabled, title_prefix=title_prefix.strip()),
    )
