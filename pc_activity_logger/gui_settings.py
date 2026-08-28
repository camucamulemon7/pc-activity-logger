from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import keyring
import yaml

from .config import Config, load_config


CREDENTIAL_SERVICE = "PCActivityLogger"
CREDENTIAL_ACCOUNT = "openwebui_api_key"


def default_app_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PCActivityLogger"
    return Path.home() / ".pc-activity-logger"


def default_config_path() -> Path:
    return default_app_directory() / "config.yaml"


def default_values() -> dict[str, Any]:
    app_dir = default_app_directory()
    return {
        "base_url": "http://localhost:8080/api",
        "model": "gemma-4-31B-it",
        "timeout_sec": 120,
        "max_tokens": 1024,
        "interval_sec": 180,
        "jpeg_quality": 80,
        "idle_threshold_sec": 300,
        "skip_same_screen": True,
        "same_screen_max_distance": 3,
        "same_screen_force_after_sec": 900,
        "skip_unavailable_session": True,
        "excluded_app_names": [],
        "excluded_window_titles": [],
        "data_dir": str(app_dir / "data"),
        "notes_enabled": True,
        "notes_title_prefix": "PC作業記録",
    }


def read_values(path: Path) -> dict[str, Any]:
    values = default_values()
    if not path.exists():
        return values
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("GUI config must contain a YAML mapping")
    ow = raw.get("openwebui", {})
    capture = raw.get("capture", {})
    storage = raw.get("storage", {})
    notes = raw.get("notes", {})
    if not all(isinstance(section, dict) for section in (ow, capture, storage, notes)):
        raise ValueError("GUI config sections must be YAML mappings")
    for key in ("base_url", "model", "timeout_sec", "max_tokens"):
        if key in ow:
            values[key] = ow[key]
    for key in (
        "interval_sec",
        "jpeg_quality",
        "idle_threshold_sec",
        "skip_same_screen",
        "same_screen_max_distance",
        "same_screen_force_after_sec",
        "skip_unavailable_session",
        "excluded_app_names",
        "excluded_window_titles",
    ):
        if key in capture:
            values[key] = capture[key]
    if "data_dir" in storage:
        data_dir = Path(str(storage["data_dir"]))
        if not data_dir.is_absolute():
            data_dir = path.resolve().parent / data_dir
        values["data_dir"] = str(data_dir)
    if "enabled" in notes:
        values["notes_enabled"] = notes["enabled"]
    if "title_prefix" in notes:
        values["notes_title_prefix"] = notes["title_prefix"]
    return values


def save_values(path: Path, values: dict[str, Any], api_key: str) -> Config:
    if not api_key.strip():
        raise ValueError("OpenWebUI API Keyを入力してください")
    path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(str(values["data_dir"])).expanduser().resolve()
    raw = {
        "openwebui": {
            "base_url": str(values["base_url"]).strip(),
            "model": str(values["model"]).strip(),
            "timeout_sec": int(values["timeout_sec"]),
            "max_tokens": int(values["max_tokens"]),
        },
        "capture": {
            "interval_sec": int(values["interval_sec"]),
            "jpeg_quality": int(values["jpeg_quality"]),
            "idle_threshold_sec": int(values["idle_threshold_sec"]),
            "skip_same_screen": bool(values["skip_same_screen"]),
            "same_screen_max_distance": int(values["same_screen_max_distance"]),
            "same_screen_force_after_sec": int(
                values["same_screen_force_after_sec"]
            ),
            "skip_unavailable_session": bool(values["skip_unavailable_session"]),
            "excluded_app_names": list(values["excluded_app_names"]),
            "excluded_window_titles": list(values["excluded_window_titles"]),
        },
        "storage": {"data_dir": str(data_dir)},
        "notes": {
            "enabled": bool(values["notes_enabled"]),
            "title_prefix": str(values["notes_title_prefix"]).strip(),
        },
    }
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    try:
        config = load_config(temporary_path, api_key_override=api_key)
        keyring.set_password(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT, api_key.strip())
        temporary_path.replace(path)
        return config
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_runtime_config(path: Path) -> Config:
    api_key = keyring.get_password(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT)
    if not api_key:
        raise ValueError("Windows資格情報マネージャーにAPI Keyが保存されていません")
    return load_config(path, api_key_override=api_key)


def stored_api_key() -> str:
    return keyring.get_password(CREDENTIAL_SERVICE, CREDENTIAL_ACCOUNT) or ""
