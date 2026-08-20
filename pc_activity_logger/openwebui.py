from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from .config import OpenWebUIConfig
from .windows import ActiveWindow


LOGGER = logging.getLogger("pc_activity_logger")
OPENWEBUI_CLIENT_USER_AGENT = "pc-activity-logger"


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
    },
    "required": ["activity", "project", "category", "detail", "confidence"],
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "project": self.project,
            "category": self.category,
            "detail": self.detail,
            "confidence": self.confidence,
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
    try:
        confidence = float(value["confidence"])
    except (TypeError, ValueError) as exc:
        raise ValueError("Model response confidence must be numeric") from exc
    return Analysis(
        activity=value["activity"].strip(),
        project=value["project"].strip(),
        category=category,
        detail=value["detail"].strip(),
        confidence=max(0.0, min(1.0, confidence)),
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
                "X-OpenWebUI-Client-User-Agent": OPENWEBUI_CLIENT_USER_AGENT,
            }
        )
        self._note_ids: dict[str, str] = {}

    @property
    def webui_root(self) -> str:
        return self.config.base_url.removesuffix("/api")

    def _notes_url(self, suffix: str = "") -> str:
        return f"{self.webui_root}/api/v1/notes{suffix}"

    def _files_url(self, suffix: str = "") -> str:
        return f"{self.webui_root}/api/v1/files{suffix}"

    def upload_temporary_image(
        self, image_bytes: bytes, captured_at: datetime
    ) -> str:
        filename = captured_at.astimezone().strftime("pc-activity-%Y%m%d-%H%M%S.jpg")
        response = self.session.post(
            self._files_url("/"),
            params={"process": "false"},
            files={"file": (filename, image_bytes, "image/jpeg")},
            timeout=self.config.timeout_sec,
        )
        _raise_for_status(response)
        data = response.json()
        file_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise ValueError("OpenWebUI did not return a File ID")
        return file_id

    def delete_file(self, file_id: str) -> None:
        response = self.session.delete(
            self._files_url(f"/{file_id}"), timeout=self.config.timeout_sec
        )
        _raise_for_status(response)

    def _find_note_by_title(self, title: str) -> dict[str, Any] | None:
        response = self.session.get(
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
        response = self.session.get(
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
            note = self._get_note(note_id)
        else:
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
                response = self.session.post(
                    self._notes_url(f"/{note_id}/update"),
                    json={"title": title, "data": {"content": {"md": new_content}}},
                    timeout=self.config.timeout_sec,
                )
                _raise_for_status(response)
        else:
            response = self.session.post(
                self._notes_url("/create"),
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
        image_data = base64.b64encode(image_bytes).decode("ascii")
        image_fingerprint = hashlib.sha256(image_bytes).hexdigest()
        image_url = file_id or f"data:image/jpeg;base64,{image_data}"
        user_text = (
            "添付したWindowsスクリーンショットから、現在の主作業を具体的に分類してください。\n"
            f"Time: {captured_at.astimezone().isoformat()}\n"
            f"Image fingerprint: {image_fingerprint}\n"
            f"Application: {window.app_name}\n"
            f"Window title: {window.title}\n"
            "ApplicationとWindow titleは前面ウィンドウの正しい情報です。画像と矛盾する無関係なアプリを回答しないでください。\n"
            "activityは『どのサービス/プロジェクトで・何を対象に・何をしているか』を1文で表してください。アプリ名だけの『閲覧』『確認』『作業』は禁止です。\n"
            "ブラウザならサイト名、ページ/記事/Issue名、対象項目、操作を含めてください。端末ならプロジェクト、コマンド、処理結果またはエラーを含めてください。IDEならリポジトリ、ファイル/設定項目、編集・調査内容を含めてください。\n"
            "activityは具体的な25～60文字の日本語にしてください。detailは画面に読める非機密な根拠（ページ名、ファイル名、コマンド、エラー、モデル名、指標など）を2～4個含む60～180文字の日本語にしてください。\n"
            "画面から読めない事実は推測せずconfidenceを下げてください。projectはリポジトリ、作業ディレクトリ、製品または業務名を優先し、判断できない場合だけunknownにしてください。機密情報やメール本文は転記しないでください。\n"
            "メール画面ではプライバシー保護のため送信者名、件名、本文をdetailへ書かず、受信トレイ等のフォルダ種別と一覧確認・検索・作成等の操作だけを記録してください。\n"
            "activity, project, category, detail, confidenceを持つJSONだけを返してください。"
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
            response = self.session.post(
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
