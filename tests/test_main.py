import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pc_activity_logger.config import (
    CaptureConfig,
    Config,
    NotesConfig,
    OpenWebUIConfig,
    StorageConfig,
)
from pc_activity_logger.main import CaptureState, run_once


class MainTests(unittest.TestCase):
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
