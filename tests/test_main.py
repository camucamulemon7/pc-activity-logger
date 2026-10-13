import unittest
import json
import tempfile
import signal
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from pc_activity_logger.config import (
    CaptureConfig,
    Config,
    NotesConfig,
    OpenWebUIConfig,
    StorageConfig,
)
from pc_activity_logger.main import CaptureState, main, run_once
from pc_activity_logger.openwebui import Analysis
from pc_activity_logger.windows import ActiveWindow


class MainTests(unittest.TestCase):
    def test_foreground_only_mode_captures_and_saves_one_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = Config(OpenWebUIConfig("http://localhost/api", "synthetic", "model"),
                            CaptureConfig(idle_threshold_sec=0), StorageConfig(root), NotesConfig())
            window = ActiveWindow(0, "synthetic", "test.exe", {
                "left": -800, "top": 0, "width": 800, "height": 600,
            }, {"left": -700, "top": 50, "width": 500, "height": 400})
            client = Mock()
            client.upload_temporary_image.return_value = "synthetic-file"
            client.analyze.return_value = Analysis("確認", "p", "other", "詳細", .8)
            with patch("pc_activity_logger.main.is_interactive_session_available", return_value=True), patch(
                "pc_activity_logger.main.get_active_window", return_value=window
            ), patch("pc_activity_logger.main.capture_monitor", return_value=b"synthetic-active") as capture, patch(
                "pc_activity_logger.main.crop_to_active_window"
            ) as crop, patch("pc_activity_logger.main.difference_hash", return_value=123):
                result = run_once(config, client)
            capture.assert_called_once_with({"left": -700, "top": 50, "width": 500, "height": 400}, 80)
            crop.assert_not_called()
            self.assertEqual(len(list(root.rglob("*.jpg"))), 1)
            self.assertNotIn("monitor_screenshot", result.record)
            self.assertEqual(result.record["interval_sec"], 180)

    def test_small_window_is_skipped_before_capture_or_upload(self) -> None:
        config = Config(OpenWebUIConfig("http://localhost/api", "synthetic", "model"),
                        CaptureConfig(idle_threshold_sec=0), StorageConfig(), NotesConfig())
        window = ActiveWindow(0, "sensitive title", "test.exe", {
            "left": 0, "top": 0, "width": 800, "height": 600,
        }, {"left": 10, "top": 10, "width": 20, "height": 20})
        client = Mock()
        with patch("pc_activity_logger.main.is_interactive_session_available", return_value=True), patch(
            "pc_activity_logger.main.get_active_window", return_value=window
        ), patch("pc_activity_logger.main.capture_monitor") as capture, self.assertLogs(
            "pc_activity_logger", level="INFO"
        ) as logs:
            run_once(config, client)
        capture.assert_not_called()
        client.upload_temporary_image.assert_not_called()
        self.assertNotIn("sensitive title", " ".join(logs.output))

    def test_cli_startup_delay_can_be_stopped_before_capture_and_closes_client(self) -> None:
        config = Config(OpenWebUIConfig("http://localhost/api", "synthetic", "model"),
                        CaptureConfig(interval_sec=60, startup_delay_min=5), StorageConfig(), NotesConfig())
        handlers = {}
        def register(signum, handler):
            handlers[signum] = handler
        def cancel(_duration):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        with patch("pc_activity_logger.main.load_config", return_value=config), patch(
            "pc_activity_logger.main.OpenWebUIClient"
        ) as create, patch("pc_activity_logger.main.signal.signal", side_effect=register), patch(
            "pc_activity_logger.main.time.monotonic", return_value=1000
        ), patch("pc_activity_logger.main.time.sleep", side_effect=cancel) as sleep, patch(
            "pc_activity_logger.main.run_once"
        ) as capture:
            self.assertEqual(main(["--background"]), 0)
        sleep.assert_called_once()
        capture.assert_not_called()
        create.return_value.close.assert_called_once()

    def test_cli_once_closes_client_after_capture_failure(self) -> None:
        config = Config(OpenWebUIConfig("http://localhost/api", "synthetic", "model"),
                        CaptureConfig(), StorageConfig(), NotesConfig())
        with patch("pc_activity_logger.main.load_config", return_value=config), patch(
            "pc_activity_logger.main.OpenWebUIClient"
        ) as create, patch("pc_activity_logger.main.run_once", side_effect=RuntimeError("synthetic")), self.assertLogs(
            "pc_activity_logger", level="ERROR"
        ):
            self.assertEqual(main(["--once"]), 1)
        create.return_value.close.assert_called_once()

    def test_failure_paths_preserve_retry_state_and_local_records(self) -> None:
        for failure in ("upload", "analysis", "storage", "delete", "note", "markdown", "markdown_disabled"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                data_dir = Path(temporary)
                config = Config(
                    OpenWebUIConfig("http://localhost/api", "test", "model"),
                    CaptureConfig(idle_threshold_sec=0, skip_same_screen=False),
                    StorageConfig(data_dir, markdown_enabled=failure != "markdown_disabled", save_monitor_screenshot=True),
                    NotesConfig(enabled=True),
                )
                window = ActiveWindow(0, "synthetic", "test.exe", {
                    "left": 0, "top": 0, "width": 100, "height": 100,
                }, {"left": 0, "top": 0, "width": 100, "height": 100})
                client = Mock()
                client.upload_temporary_image.return_value = "file-test"
                client.analyze.return_value = Analysis("確認", "p", "other", "詳細", .8)
                operations = {
                    "upload": client.upload_temporary_image,
                    "analysis": client.analyze,
                    "delete": client.delete_file,
                    "note": client.append_daily_note,
                }
                if failure in operations:
                    operations[failure].side_effect = RuntimeError(failure)
                state = CaptureState(last_analyzed_hash=999, last_analyzed_at=0.0)
                with ExitStack() as stack:
                    for name, result in (
                        ("is_interactive_session_available", True),
                        ("get_active_window", window),
                        ("capture_monitor", b"synthetic-monitor"),
                        ("crop_to_active_window", b"synthetic-active"),
                        ("difference_hash", 123),
                    ):
                        stack.enter_context(patch(f"pc_activity_logger.main.{name}", return_value=result))
                    if failure == "storage":
                        stack.enter_context(patch("pc_activity_logger.main.append_activity",
                                                  side_effect=RuntimeError("storage")))
                    if failure == "markdown":
                        stack.enter_context(patch("pc_activity_logger.main.write_activity_markdown",
                                                  side_effect=OSError("markdown")))
                    with self.assertLogs("pc_activity_logger", level="INFO"):
                        if failure in ("upload", "analysis", "storage"):
                            with self.assertRaisesRegex(RuntimeError, failure):
                                run_once(config, client, state)
                        else:
                            run_once(config, client, state)
                if failure == "upload":
                    client.delete_file.assert_not_called()
                else:
                    client.delete_file.assert_called_once_with("file-test")
                if failure in ("upload", "analysis", "storage"):
                    self.assertEqual((state.last_analyzed_hash, state.last_analyzed_at), (999, 0.0))
                    self.assertEqual(list(data_dir.rglob("activity.jsonl")), [])
                    client.append_daily_note.assert_not_called()
                else:
                    self.assertEqual(state.last_analyzed_hash, 123)
                    records = list(data_dir.rglob("activity.jsonl"))
                    self.assertEqual(len(records), 1)
                    self.assertIn('"confidence":0.8', records[0].read_text(encoding="utf-8"))
                    markdown_paths = list((data_dir / "obsidian" / "PC Activity Logger" / "Records").rglob("*.md"))
                    if failure in ("markdown", "markdown_disabled"):
                        self.assertEqual(markdown_paths, [])
                    else:
                        self.assertEqual(len(markdown_paths), 1)
                        self.assertIn("確認", markdown_paths[0].read_text(encoding="utf-8"))
                    client.append_daily_note.assert_called_once()

    def test_skips_excluded_app_before_screenshot(self) -> None:
        config = Config(
            openwebui=OpenWebUIConfig(
                "http://localhost:8080/api", "secret", "model"
            ),
            capture=CaptureConfig(
                idle_threshold_sec=0, excluded_app_names=("1password",)
            ),
            storage=StorageConfig(Path("data")),
            notes=NotesConfig(),
        )
        window = Mock(app_name="1Password.EXE", title="Password Manager")
        client = Mock()

        with (
            patch(
                "pc_activity_logger.main.is_interactive_session_available",
                return_value=True,
            ),
            patch("pc_activity_logger.main.get_active_window", return_value=window),
            patch("pc_activity_logger.main.capture_monitor") as capture,
        ):
            run_once(config, client)

        capture.assert_not_called()
        client.upload_temporary_image.assert_not_called()

    def test_skips_excluded_window_title_before_screenshot(self) -> None:
        config = Config(
            openwebui=OpenWebUIConfig(
                "http://localhost:8080/api", "secret", "model"
            ),
            capture=CaptureConfig(
                idle_threshold_sec=0,
                excluded_window_titles=("機密プロジェクト",),
            ),
            storage=StorageConfig(Path("data")),
            notes=NotesConfig(),
        )
        window = Mock(app_name="chrome.exe", title="機密プロジェクト - Chrome")
        client = Mock()

        with (
            patch(
                "pc_activity_logger.main.is_interactive_session_available",
                return_value=True,
            ),
            patch("pc_activity_logger.main.get_active_window", return_value=window),
            patch("pc_activity_logger.main.capture_monitor") as capture,
        ):
            run_once(config, client)

        capture.assert_not_called()
        client.upload_temporary_image.assert_not_called()

    def test_skips_entire_cycle_when_user_is_idle(self) -> None:
        config = Config(
            openwebui=OpenWebUIConfig(
                "http://localhost:8080/api", "secret", "model"
            ),
            capture=CaptureConfig(idle_threshold_sec=300),
            storage=StorageConfig(Path("data")),
            notes=NotesConfig(),
        )
        client = Mock()

        with (
            patch(
                "pc_activity_logger.main.is_interactive_session_available",
                return_value=True,
            ),
            patch("pc_activity_logger.main.get_idle_seconds", return_value=301),
            patch("pc_activity_logger.main.get_active_window") as active_window,
            patch("pc_activity_logger.main.capture_monitor") as capture,
        ):
            run_once(config, client)

        active_window.assert_not_called()
        capture.assert_not_called()
        client.upload_temporary_image.assert_not_called()
        client.analyze.assert_not_called()

    def test_deletes_openwebui_file_when_analysis_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            config = Config(
                openwebui=OpenWebUIConfig(
                    "http://localhost:8080/api", "secret", "model"
                ),
                capture=CaptureConfig(idle_threshold_sec=0),
                storage=StorageConfig(data_dir),
                notes=NotesConfig(),
            )
            client = Mock()
            client.upload_temporary_image.return_value = "file-123"
            client.analyze.side_effect = ValueError("invalid JSON")
            window = Mock()
            window.app_name = "app.exe"
            window.title = "title"
            window.monitor = {"left": 0, "top": 0, "width": 100, "height": 100}
            window.window_rect = window.monitor

            with (
                patch(
                    "pc_activity_logger.main.is_interactive_session_available",
                    return_value=True,
                ),
                patch("pc_activity_logger.main.get_active_window", return_value=window),
                patch("pc_activity_logger.main.capture_monitor", return_value=b"monitor"),
                patch(
                    "pc_activity_logger.main.crop_to_active_window",
                    return_value=b"active",
                ),
                patch("pc_activity_logger.main.difference_hash", return_value=123),
                patch(
                    "pc_activity_logger.main.save_screenshot",
                    side_effect=[data_dir / "monitor.jpg", data_dir / "active.jpg"],
                ),
            ):
                with self.assertRaisesRegex(ValueError, "invalid JSON"):
                    run_once(config, client, CaptureState())

            client.delete_file.assert_called_once_with("file-123")

    def test_skips_entire_cycle_when_session_is_unavailable(self) -> None:
        config = Config(
            openwebui=OpenWebUIConfig(
                "http://localhost:8080/api", "secret", "model"
            ),
            capture=CaptureConfig(),
            storage=StorageConfig(Path("data")),
            notes=NotesConfig(),
        )
        client = Mock()

        with (
            patch(
                "pc_activity_logger.main.is_interactive_session_available",
                return_value=False,
            ),
            patch("pc_activity_logger.main.get_idle_seconds") as idle,
            patch("pc_activity_logger.main.get_active_window") as active_window,
        ):
            run_once(config, client, CaptureState())

        idle.assert_not_called()
        active_window.assert_not_called()
        client.upload_temporary_image.assert_not_called()

    def test_skips_unchanged_screen_before_saving_or_uploading(self) -> None:
        config = Config(
            openwebui=OpenWebUIConfig(
                "http://localhost:8080/api", "secret", "model"
            ),
            capture=CaptureConfig(idle_threshold_sec=0),
            storage=StorageConfig(Path("data")),
            notes=NotesConfig(),
        )
        client = Mock()
        state = CaptureState(last_analyzed_hash=123, last_analyzed_at=100.0)
        window = Mock()
        window.app_name = "app.exe"
        window.title = "title"
        window.monitor = {"left": 0, "top": 0, "width": 100, "height": 100}
        window.window_rect = window.monitor

        with (
            patch(
                "pc_activity_logger.main.is_interactive_session_available",
                return_value=True,
            ),
            patch("pc_activity_logger.main.get_active_window", return_value=window),
            patch("pc_activity_logger.main.capture_monitor", return_value=b"monitor"),
            patch(
                "pc_activity_logger.main.crop_to_active_window",
                return_value=b"active",
            ),
            patch("pc_activity_logger.main.difference_hash", return_value=123),
            patch("pc_activity_logger.main.time.monotonic", return_value=200.0),
            patch("pc_activity_logger.main.save_screenshot") as save,
        ):
            run_once(config, client, state)

        save.assert_not_called()
        client.upload_temporary_image.assert_not_called()
        client.analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
