import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, call, patch

import requests

from pc_activity_logger.config import OpenWebUIConfig
from pc_activity_logger.openwebui import OpenWebUIClient


def response(status=200, data=None, headers=None):
    result = Mock(spec=requests.Response, status_code=status, headers=headers or {}, text="synthetic")
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError("synthetic", response=result)
    return result


class NetworkTests(unittest.TestCase):
    def test_connection_timeout_and_transient_http_are_retried_with_bounded_backoff(self) -> None:
        for failure in (requests.ConnectionError("synthetic"), requests.Timeout("synthetic"),
                        response(503, headers={"Retry-After": "999"})):
            with self.subTest(failure=failure):
                client = OpenWebUIClient(OpenWebUIConfig("http://localhost/api", "synthetic", "model"))
                client.session.get = Mock(side_effect=[failure, failure, response(200, {"data": [{"id": "model"}]})])
                with patch("pc_activity_logger.openwebui.time.sleep") as sleep:
                    self.assertEqual(client.list_models(), ["model"])
                expected = [call(30.0), call(30.0)] if isinstance(failure, Mock) else [call(1.0), call(2.0)]
                self.assertEqual(sleep.call_args_list, expected)
                self.assertEqual(client.session.get.call_count, 3)

    def test_authentication_errors_and_uploads_are_not_retried(self) -> None:
        client = OpenWebUIClient(OpenWebUIConfig("http://localhost/api", "synthetic", "model"))
        client.session.get = Mock(return_value=response(401))
        with self.assertRaises(requests.HTTPError):
            client.list_models()
        client.session.get.assert_called_once()
        client.session.post = Mock(side_effect=requests.Timeout("synthetic"))
        with self.assertRaises(requests.Timeout):
            client.upload_temporary_image(b"synthetic", datetime.now(timezone.utc))
        client.session.post.assert_called_once()

    def test_retry_limit_is_respected(self) -> None:
        client = OpenWebUIClient(OpenWebUIConfig("http://localhost/api", "synthetic", "model", max_retries=2))
        client.session.get = Mock(side_effect=requests.Timeout("synthetic"))
        with patch("pc_activity_logger.openwebui.time.sleep"), self.assertRaises(requests.Timeout):
            client.list_models()
        self.assertEqual(client.session.get.call_count, 3)

    def test_cleanup_only_deletes_old_owned_logger_images_after_all_pages_are_read(self) -> None:
        client = OpenWebUIClient(OpenWebUIConfig("http://localhost/api", "synthetic", "model"))
        now = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        old = now.timestamp() - 48 * 3600
        item = {"id": "old", "user_id": "me", "filename": "pc-activity-20261012-120000.jpg", "created_at": old}
        items = [item, {**item, "id": "other-user", "user_id": "other"},
                 {**item, "id": "recent", "created_at": now.timestamp()},
                 {**item, "id": "private", "filename": "personal.jpg"},
                 {**item, "id": "unknown-age", "created_at": None},
                 {**item, "id": "active"}]
        client._temporary_file_ids.add("active")
        client.session.get = Mock(side_effect=[response(data={"id": "me"}),
            response(data={"items": items, "total": 7}),
            response(data={"items": [{**item, "id": "second"}], "total": 7})])
        def delete(_url, **_kwargs):
            self.assertEqual(client.session.get.call_count, 3)
            return response(200)
        client.session.delete = Mock(side_effect=delete)
        self.assertEqual(client.cleanup_temporary_files(now), 2)
        self.assertEqual([value.args[0].rsplit("/", 1)[-1] for value in client.session.delete.call_args_list], ["old", "second"])
        self.assertEqual(client.session.get.call_args.kwargs["params"], {"page": 2, "content": "false"})

    def test_cleanup_refuses_unverified_owner_and_delete_404_is_success(self) -> None:
        client = OpenWebUIClient(OpenWebUIConfig("http://localhost/api", "synthetic", "model"))
        client.session.get = Mock(return_value=response(data={}))
        client.session.delete = Mock(return_value=response(404))
        with self.assertRaises(ValueError):
            client.cleanup_temporary_files()
        client.session.delete.assert_not_called()
        client.delete_file("already-gone")
        client.session.delete.assert_called_once()
