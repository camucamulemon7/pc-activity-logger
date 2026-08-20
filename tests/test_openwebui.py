import unittest
from unittest.mock import Mock

import requests

from pc_activity_logger.config import OpenWebUIConfig
from pc_activity_logger.openwebui import (
    Analysis,
    OpenWebUIClient,
    _extract_json,
    _message_text,
    _raise_for_status,
    _validate,
)
from pc_activity_logger.windows import ActiveWindow


class ModelResponseTests(unittest.TestCase):
    def test_sets_openwebui_client_user_agent_header(self) -> None:
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost:8080/api", "secret", "model")
        )
        self.assertEqual(
            client.session.headers["X-OpenWebUI-Client-User-Agent"],
            "pc-activity-logger",
        )

    def test_extracts_plain_json(self) -> None:
        value = _extract_json(
            '{"activity":"確認","project":"p","category":"development",'
            '"detail":"RTL確認","confidence":0.9}'
        )
        self.assertEqual(value["activity"], "確認")

    def test_extracts_fenced_json(self) -> None:
        value = _extract_json('```json\n{"activity":"確認"}\n```')
        self.assertEqual(value["activity"], "確認")

    def test_validation_clamps_confidence_and_category(self) -> None:
        result = _validate(
            {
                "activity": "確認",
                "project": "p",
                "category": "unexpected",
                "detail": "詳細を確認している",
                "confidence": 2,
            }
        )
        self.assertEqual(result.category, "other")
        self.assertEqual(result.confidence, 1.0)

    def test_validation_rejects_non_japanese_activity(self) -> None:
        with self.assertRaisesRegex(ValueError, "written in Japanese"):
            _validate(
                {
                    "activity": "Reviewing dashboard",
                    "project": "p",
                    "category": "research",
                    "detail": "ダッシュボードを確認している",
                    "confidence": 0.9,
                }
            )

    def test_extracts_json_surrounded_by_model_commentary(self) -> None:
        value = _extract_json(
            'Result:\n{"activity":"確認","project":"p","category":"development",'
            '"detail":"RTL確認","confidence":0.9}\nDone.'
        )
        self.assertEqual(value["project"], "p")

    def test_http_error_includes_response_body(self) -> None:
        response = Mock(spec=requests.Response)
        response.status_code = 400
        response.text = '{"detail":"Model not found"}'
        response.raise_for_status.side_effect = requests.HTTPError("400")
        with self.assertRaisesRegex(requests.HTTPError, "Model not found"):
            _raise_for_status(response)

    def test_accepts_list_content(self) -> None:
        content = _message_text(
            {"content": [{"type": "text", "text": "first"}, {"text": "second"}]}
        )
        self.assertEqual(content, "first\nsecond")

    def test_retries_null_content_once(self) -> None:
        invalid = Mock(spec=requests.Response)
        invalid.status_code = 200
        invalid.raise_for_status.return_value = None
        invalid.json.return_value = {"choices": [{"message": {"content": None}}]}
        valid = Mock(spec=requests.Response)
        valid.status_code = 200
        valid.raise_for_status.return_value = None
        valid.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"activity":"確認","project":"p",'
                            '"category":"development","detail":"詳細",'
                            '"confidence":0.9}'
                        )
                    }
                }
            ]
        }
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost/api", "secret", "model")
        )
        client.session.post = Mock(side_effect=[invalid, valid])
        window = ActiveWindow(
            0,
            "title",
            "app.exe",
            {"left": 0, "top": 0, "width": 100, "height": 100},
        )
        result = client.analyze(b"image", __import__("datetime").datetime.now(), window)
        self.assertEqual(result.activity, "確認")
        self.assertEqual(client.session.post.call_count, 2)
        payload = client.session.post.call_args_list[0].kwargs["json"]
        self.assertEqual(len(payload["messages"]), 1)
        user_content = payload["messages"][0]["content"]
        self.assertEqual(user_content[0]["type"], "image_url")
        self.assertEqual(user_content[1]["type"], "text")
        self.assertEqual(len(user_content[0]["uuid"]), 64)
        self.assertIn(user_content[0]["uuid"], user_content[1]["text"])
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertEqual(payload["temperature"], 0)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        retry_payload = client.session.post.call_args_list[1].kwargs["json"]
        self.assertEqual(len(retry_payload["messages"]), 2)
        self.assertIn("空または利用不能", retry_payload["messages"][1]["content"])

    def test_retries_malformed_json_as_correction_conversation(self) -> None:
        invalid = Mock(spec=requests.Response)
        invalid.status_code = 200
        invalid.raise_for_status.return_value = None
        broken = '{"activity":"画面を確認","project":"p" "category":"other"}'
        invalid.json.return_value = {
            "choices": [{"message": {"content": broken}}]
        }
        valid = Mock(spec=requests.Response)
        valid.status_code = 200
        valid.raise_for_status.return_value = None
        valid.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"activity":"画面を確認している","project":"p",'
                            '"category":"other","detail":"画面の詳細を確認している",'
                            '"confidence":0.8}'
                        )
                    }
                }
            ]
        }
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost/api", "secret", "model")
        )
        client.session.post = Mock(side_effect=[invalid, valid])
        window = ActiveWindow(
            0,
            "title",
            "app.exe",
            {"left": 0, "top": 0, "width": 100, "height": 100},
        )

        result = client.analyze(b"image", __import__("datetime").datetime.now(), window)

        self.assertEqual(result.confidence, 0.8)
        retry_messages = client.session.post.call_args_list[1].kwargs["json"][
            "messages"
        ]
        self.assertEqual(retry_messages[1], {"role": "assistant", "content": broken})
        self.assertIn("有効なJSON", retry_messages[2]["content"])

    def test_creates_daily_openwebui_note(self) -> None:
        notes_list = Mock(spec=requests.Response)
        notes_list.status_code = 200
        notes_list.raise_for_status.return_value = None
        notes_list.json.return_value = []
        created = Mock(spec=requests.Response)
        created.status_code = 200
        created.raise_for_status.return_value = None
        created.json.return_value = {"id": "note-123"}
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost:8080/api", "secret", "model")
        )
        client.session.get = Mock(return_value=notes_list)
        client.session.post = Mock(return_value=created)
        window = ActiveWindow(
            0,
            "さくらのAI Engine - Google Chrome",
            "chrome.exe",
            {"left": 0, "top": 0, "width": 100, "height": 100},
        )
        note_id = client.append_daily_note(
            __import__("datetime").datetime.fromisoformat(
                "2026-08-20T23:00:00+09:00"
            ),
            window,
            Analysis("モデル利用量を確認", "AI Engine", "administration", "詳細", 0.9),
            "PC作業記録",
        )
        self.assertEqual(note_id, "note-123")
        request = client.session.post.call_args
        self.assertTrue(request.args[0].endswith("/api/v1/notes/create"))
        self.assertEqual(request.kwargs["json"]["title"], "PC作業記録 2026-08-20")
        markdown = request.kwargs["json"]["data"]["content"]["md"]
        self.assertIn("モデル利用量を確認", markdown)
        self.assertIn("23:00:00", markdown)

    def test_uploads_and_deletes_temporary_image(self) -> None:
        uploaded = Mock(spec=requests.Response)
        uploaded.status_code = 200
        uploaded.raise_for_status.return_value = None
        uploaded.json.return_value = {"id": "file-123"}
        deleted = Mock(spec=requests.Response)
        deleted.status_code = 200
        deleted.raise_for_status.return_value = None
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost:8080/api", "secret", "model")
        )
        client.session.post = Mock(return_value=uploaded)
        client.session.delete = Mock(return_value=deleted)
        captured_at = __import__("datetime").datetime.fromisoformat(
            "2026-08-20T23:00:00+09:00"
        )
        file_id = client.upload_temporary_image(b"jpeg", captured_at)
        self.assertEqual(file_id, "file-123")
        upload_call = client.session.post.call_args
        self.assertTrue(upload_call.args[0].endswith("/api/v1/files/"))
        self.assertEqual(upload_call.kwargs["params"], {"process": "false"})
        self.assertEqual(upload_call.kwargs["files"]["file"][2], "image/jpeg")
        client.delete_file(file_id)
        self.assertTrue(
            client.session.delete.call_args.args[0].endswith(
                "/api/v1/files/file-123"
            )
        )

    def test_uses_uploaded_file_id_for_vision_input(self) -> None:
        valid = Mock(spec=requests.Response)
        valid.status_code = 200
        valid.raise_for_status.return_value = None
        valid.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"activity":"画面を確認している","project":"p",'
                            '"category":"development","detail":"詳細を確認している",'
                            '"confidence":0.9}'
                        )
                    }
                }
            ]
        }
        client = OpenWebUIClient(
            OpenWebUIConfig("http://localhost:8080/api", "secret", "model")
        )
        client.session.post = Mock(return_value=valid)
        window = ActiveWindow(
            0,
            "title",
            "app.exe",
            {"left": 0, "top": 0, "width": 100, "height": 100},
        )
        client.analyze(
            b"image",
            __import__("datetime").datetime.now(),
            window,
            file_id="file-123",
        )
        payload = client.session.post.call_args.kwargs["json"]
        self.assertEqual(
            payload["messages"][0]["content"][0]["image_url"]["url"],
            "file-123",
        )


if __name__ == "__main__":
    unittest.main()
