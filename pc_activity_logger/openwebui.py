from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import requests

from .config import OpenWebUIConfig
from .windows import ActiveWindow


LOGGER = logging.getLogger("pc_activity_logger")
OPENWEBUI_CLIENT_USER_AGENT = "pc-activity-logger"
RETRY_STATUSES = {429, 502, 503, 504}
TEMPORARY_IMAGE_NAME = re.compile(r"pc-activity-\d{8}-\d{6}(?:-[a-f0-9]{32})?\.jpg\Z")


REQUIRED_KEYS = {"activity", "project", "category", "detail", "confidence"}
JAPANESE_TEXT = re.compile(r"[ぁ-んァ-ヶ一-龠々]")
ALLOWED_CATEGORIES = {
    "development",
    "communication",
    "research",
    "documentation",
    "administration",
    "meeting",
    "other",
}

ACTIVITY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "activity": {"type": "string", "minLength": 1},
        "project": {"type": "string", "minLength": 1},
        "category": {"type": "string", "enum": sorted(ALLOWED_CATEGORIES)},
        "detail": {"type": "string", "minLength": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "keywords": {
            "type": "array", "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
        },
    },
    "required": ["activity", "project", "category", "detail", "confidence", "keywords"],
    "additionalProperties": False,
}


def _raise_for_status(response: requests.Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text.strip().replace("\r", " ").replace("\n", " ")
        if len(body) > 1000:
            body = body[:1000] + "..."
        message = f"OpenWebUI returned HTTP {response.status_code}"
        if body:
            message += f": {body}"
        raise requests.HTTPError(message, response=response) from exc


@dataclass(frozen=True)
class Analysis:
    activity: str
    project: str
    category: str
    detail: str
    confidence: float
    keywords: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "project": self.project,
            "category": self.category,
            "detail": self.detail,
            "confidence": self.confidence,
            "keywords": list(self.keywords),
        }


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = cleaned.find("{")
        if start < 0:
            raise ValueError("Model response did not contain a JSON object")
        try:
            value, _ = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError as exc:
            raise ValueError("Model response contained invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Model response JSON must be an object")
    return value


def _validate(value: dict[str, Any]) -> Analysis:
    missing = REQUIRED_KEYS - value.keys()
    if missing:
        raise ValueError(f"Model response is missing keys: {sorted(missing)}")
    for key in ("activity", "project", "category", "detail"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"Model response field '{key}' must be a non-empty string")
    for key in ("activity", "detail"):
        if not JAPANESE_TEXT.search(value[key]):
            raise ValueError(f"Model response field '{key}' must be written in Japanese")
    category = value["category"].lower()
    if category not in ALLOWED_CATEGORIES:
        category = "other"
    if isinstance(value["confidence"], bool):
        raise ValueError("Model response confidence must be numeric")
    try:
        confidence = float(value["confidence"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Model response confidence must be numeric") from exc
    if not math.isfinite(confidence):
        raise ValueError("Model response confidence must be finite")
    keyword_values = value.get("keywords", [])
    if not isinstance(keyword_values, list) or len(keyword_values) > 8:
        raise ValueError("Model response keywords must be a list of at most 8 strings")
    keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keyword_values:
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword.strip()) > 64:
            raise ValueError("Model response keywords must contain non-empty strings of at most 64 characters")
        keyword = unicodedata.normalize("NFKC", " ".join(keyword.split()))
        if len(keyword) > 64:
            raise ValueError("Model response keywords must be at most 64 characters after normalization")
        if keyword.casefold() not in seen:
            seen.add(keyword.casefold())
            keywords.append(keyword)
    return Analysis(
        activity=value["activity"].strip(),
        project=value["project"].strip(),
        category=category,
        detail=value["detail"].strip(),
        confidence=max(0.0, min(1.0, confidence)),
        keywords=tuple(keywords),
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text", item.get("content"))
                if isinstance(text, str):
                    parts.append(text)
        combined = "\n".join(part for part in parts if part.strip())
        if combined:
            return combined
    if isinstance(content, dict):
        text = content.get("text", content.get("content"))
        if isinstance(text, str) and text.strip():
            return text
    # Some reasoning-model proxies route constrained JSON to reasoning_content.
    # Accept only a complete, schema-valid result, never reasoning prose.
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str):
        try:
            value = json.loads(reasoning)
        except (ValueError, TypeError):
            value = None
        if (isinstance(value, dict) and REQUIRED_KEYS <= value.keys()
                and value.keys() <= REQUIRED_KEYS | {"keywords"}):
            _validate(value)
            return reasoning
    keys = sorted(str(key) for key in message.keys())
    raise ValueError(
        "OpenWebUI message content was unusable "
        f"(type={type(content).__name__}, message_keys={keys})"
    )


class OpenWebUIClient:
    def __init__(self, config: OpenWebUIConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "User-Agent": OPENWEBUI_CLIENT_USER_AGENT,
                "X-OpenWebUI-Client-User-Agent": OPENWEBUI_CLIENT_USER_AGENT,
            }
        )
        self._note_ids: dict[str, str] = {}
        self._temporary_file_ids: set[str] = set()

    def _request(self, method: str, url: str, *, retry: bool = True, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.config.timeout_sec)
        retries = self.config.max_retries if retry else 0
        for attempt in range(retries + 1):
            retry_after = 0.0
            try:
                response = getattr(self.session, method)(url, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if attempt >= retries:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= retries:
                    return response
                try:
                    retry_after = float(response.headers.get("Retry-After", "0"))
                    if not math.isfinite(retry_after):
                        retry_after = 0.0
                except (ValueError, TypeError):
                    retry_after = 0.0
                response.close()
            delay = min(30.0, max(retry_after, self.config.retry_backoff_sec * (2 ** attempt)))
            LOGGER.warning("Transient OpenWebUI %s failure; retry %d/%d in %.1f seconds",
                           method.upper(), attempt + 1, retries, delay)
            time.sleep(delay)
        raise AssertionError("Unreachable request retry state")

    def close(self) -> None:
        self.session.close()

    @property
    def webui_root(self) -> str:
        return self.config.base_url.removesuffix("/api")

    def _notes_url(self, suffix: str = "") -> str:
        return f"{self.webui_root}/api/v1/notes{suffix}"

    def _files_url(self, suffix: str = "") -> str:
        return f"{self.webui_root}/api/v1/files{suffix}"

    def list_models(self) -> list[str]:
        response = self._request("get",
            f"{self.config.base_url}/models", timeout=self.config.timeout_sec
        )
        _raise_for_status(response)
        payload = response.json()
        items = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ValueError("Unexpected OpenWebUI models response")
        return sorted(
            item["id"]
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
        )

    def upload_temporary_image(
        self, image_bytes: bytes, captured_at: datetime
    ) -> str:
        filename = captured_at.astimezone().strftime("pc-activity-%Y%m%d-%H%M%S") + f"-{uuid4().hex}.jpg"
        response = self._request("post",
            self._files_url("/"),
            retry=False,  # A lost upload response must not create duplicate remote files.
            params={"process": "false"},
            files={"file": (filename, image_bytes, "image/jpeg")},
            timeout=self.config.timeout_sec,
        )
        _raise_for_status(response)
        data = response.json()
        file_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("OpenWebUI did not return a File ID")
        self._temporary_file_ids.add(file_id)
        return file_id

    def delete_file(self, file_id: str) -> None:
        response = self._request("delete",
            self._files_url(f"/{quote(file_id, safe='')}"), timeout=self.config.timeout_sec
        )
        if response.status_code != 404:
            _raise_for_status(response)
        self._temporary_file_ids.discard(file_id)

    def cleanup_temporary_files(self, now: datetime | None = None) -> int:
        """Delete only old, owned logger images, collecting pages before deleting."""
        response = self._request("get", f"{self.webui_root}/api/v1/auths/")
        _raise_for_status(response)
        user = response.json()
        owner = user.get("id") if isinstance(user, dict) else None
        if not isinstance(owner, str) or not owner:
            raise ValueError("OpenWebUI did not return the authenticated user ID")
        current = (now or datetime.now(timezone.utc)).timestamp()
        cutoff = current - self.config.temporary_file_max_age_hours * 3600
        candidates: list[str] = []
        seen: set[str] = set()
        for page in range(1, 101):
            response = self._request("get", self._files_url("/"), params={"page": page, "content": "false"})
            _raise_for_status(response)
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else payload
            if not isinstance(items, list):
                raise ValueError("Unexpected OpenWebUI Files list response")
            new_ids = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                file_id = item.get("id")
                if not isinstance(file_id, str) or not file_id or file_id in seen:
                    continue
                seen.add(file_id)
                new_ids += 1
                if (item.get("user_id") != owner or file_id in self._temporary_file_ids
                        or not isinstance(item.get("filename"), str)
                        or not TEMPORARY_IMAGE_NAME.fullmatch(item["filename"])):
                    continue
                created = item.get("created_at")
                if isinstance(created, bool) or not isinstance(created, (int, float)):
                    continue
                try:
                    old_enough = math.isfinite(created) and 0 < created < cutoff
                except OverflowError:
                    old_enough = False
                if old_enough:
                    candidates.append(file_id)
            if not items or not new_ids or isinstance(payload, list):
                break
            total = payload.get("total")
            if isinstance(total, int) and len(seen) >= total:
                break
        removed = 0
        for file_id in candidates:
            try:
                self.delete_file(file_id)
                removed += 1
            except requests.RequestException:
                LOGGER.warning("Could not delete an expired OpenWebUI logger image", exc_info=True)
        return removed

    def _find_note_by_title(self, title: str) -> dict[str, Any] | None:
        response = self._request("get",
            self._notes_url("/"), timeout=self.config.timeout_sec
        )
        _raise_for_status(response)
        data = response.json()
        notes = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(notes, list):
            raise ValueError("Unexpected OpenWebUI Notes list response")
        for note in notes:
            if isinstance(note, dict) and note.get("title") == title:
                return note
        return None

    def _get_note(self, note_id: str) -> dict[str, Any]:
        response = self._request("get",
            self._notes_url(f"/{note_id}"), timeout=self.config.timeout_sec
        )
        _raise_for_status(response)
        note = response.json()
        if not isinstance(note, dict):
            raise ValueError("Unexpected OpenWebUI Note response")
        return note

    def append_daily_note(
        self,
        captured_at: datetime,
        window: ActiveWindow,
        analysis: Analysis,
        title_prefix: str,
    ) -> str:
        local_time = captured_at.astimezone()
        title = f"{title_prefix} {local_time.date().isoformat()}"
        marker = f"<!-- pc-activity:{captured_at.isoformat()} -->"
        entry = (
            f"{marker}\n"
            f"## {local_time.strftime('%H:%M:%S')} — {analysis.activity}\n\n"
            f"- **プロジェクト:** {analysis.project}\n"
            f"- **カテゴリ:** {analysis.category}\n"
            f"- **アプリ:** {window.app_name}\n"
            f"- **ウィンドウ:** {window.title}\n"
            f"- **信頼度:** {analysis.confidence:.2f}\n\n"
            f"{analysis.detail}\n"
        )

        note_id = self._note_ids.get(title)
        note: dict[str, Any] | None = None
        if note_id:
            try:
                note = self._get_note(note_id)
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
                self._note_ids.pop(title, None)
                note_id = None
        if not note_id:
            note = self._find_note_by_title(title)
            if note and isinstance(note.get("id"), str):
                note_id = note["id"]
                note = self._get_note(note_id)

        if note_id and note:
            content = ((note.get("data") or {}).get("content") or {}).get("md", "")
            if not isinstance(content, str):
                content = ""
            if marker not in content:
                new_content = content.rstrip() + "\n\n" + entry if content.strip() else entry
                response = self._request("post",
                    self._notes_url(f"/{note_id}/update"),
                    json={"title": title, "data": {"content": {"md": new_content}}},
                    timeout=self.config.timeout_sec,
                )
                _raise_for_status(response)
        else:
            response = self._request("post",
                self._notes_url("/create"),
                retry=False,  # Note creation has no idempotency key.
                json={"title": title, "data": {"content": {"md": f"# {title}\n\n{entry}"}}},
                timeout=self.config.timeout_sec,
            )
            _raise_for_status(response)
            created = response.json()
            note_id = created.get("id") if isinstance(created, dict) else None
            if not isinstance(note_id, str) or not note_id:
                raise ValueError("OpenWebUI did not return a Note ID")

        self._note_ids[title] = note_id
        return note_id

    def analyze(
        self,
        image_bytes: bytes,
        captured_at: datetime,
        window: ActiveWindow,
        file_id: str | None = None,
    ) -> Analysis:
        image_fingerprint = hashlib.sha256(image_bytes).hexdigest()
        image_url = file_id or f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"
        user_text = (
            "次の前面ウィンドウ情報と添付画像を解析してください。\n"
            f"Time: {captured_at.astimezone().isoformat()}\n"
            f"Image fingerprint: {image_fingerprint}\n"
            f"Application: {window.app_name}\n"
            f"Window title: {window.title}\n"
            "keywordsには画像で確認できる重要な技術・概念・製品名を最大8個、"
            "短い語句の配列で返してください。人名・メールアドレス・認証情報・"
            "顧客情報・機密語は除外してください。"
        )
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": self.config.max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "pc_activity",
                    "strict": True,
                    "schema": ACTIVITY_JSON_SCHEMA,
                },
            },
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url},
                            "uuid": image_fingerprint,
                        },
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
        }
        last_error: ValueError | None = None
        for attempt in range(2):
            response_text: str | None = None
            response = self._request("post",
                f"{self.config.base_url}/chat/completions",
                json=copy.deepcopy(payload),
                timeout=self.config.timeout_sec,
            )
            _raise_for_status(response)
            try:
                data = response.json()
                message = data["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError("OpenWebUI response message was not an object")
                response_text = _message_text(message)
                return _validate(_extract_json(response_text))
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                last_error = ValueError("Unexpected OpenWebUI response format")
                last_error.__cause__ = exc
            except ValueError as exc:
                last_error = exc
            if attempt == 0:
                LOGGER.warning("Unusable model response; retrying once: %s", last_error)
                if response_text:
                    payload["messages"].extend(
                        [
                            {"role": "assistant", "content": response_text},
                            {
                                "role": "user",
                                "content": (
                                    "前の応答は有効なJSONではありません。内容を見直し、"
                                    "指定スキーマに完全一致する有効なJSONオブジェクトだけを"
                                    "返してください。文字列内の引用符は必ずエスケープしてください。"
                                ),
                            },
                        ]
                    )
                else:
                    payload["messages"].append(
                        {
                            "role": "user",
                            "content": (
                                "前の応答は空または利用不能でした。画像を再確認し、"
                                "指定スキーマに完全一致する有効なJSONオブジェクトだけを"
                                "返してください。"
                            ),
                        }
                    )
        assert last_error is not None
        raise last_error
