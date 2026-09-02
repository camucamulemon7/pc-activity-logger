import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pc_activity_logger.gui import ActivityWorker


class ActivityWorkerTests(unittest.TestCase):
    def test_updates_running_configuration(self) -> None:
        original = SimpleNamespace(name="original")
        updated = SimpleNamespace(name="updated")
        worker = ActivityWorker(original)

        worker.update_config(updated)

        self.assertIs(worker.current_config(), updated)

    def test_can_stop_during_automatic_startup_delay_without_capture(self) -> None:
        config = SimpleNamespace(
            openwebui=Mock(),
            capture=SimpleNamespace(interval_sec=180),
        )
        worker = ActivityWorker(config, initial_delay_sec=180)
        worker._stop_event = Mock()
        worker._stop_event.wait.return_value = True

        with (
            patch("pc_activity_logger.gui.OpenWebUIClient"),
            patch("pc_activity_logger.gui.run_once") as run_once,
        ):
            worker.run()

        worker._stop_event.wait.assert_called_once_with(180)
        run_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
