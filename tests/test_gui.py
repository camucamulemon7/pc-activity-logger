import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pc_activity_logger.gui import ActivityWorker, ConnectionWorker, MainWindow


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

    def test_configuration_change_reanalyzes_unchanged_screen(self) -> None:
        original = SimpleNamespace(openwebui=Mock(), capture=SimpleNamespace(interval_sec=0))
        updated = SimpleNamespace(openwebui=Mock(), capture=SimpleNamespace(interval_sec=0))
        worker = ActivityWorker(original)
        calls = []

        def capture_cycle(config, client, state):
            calls.append((config, state.last_analyzed_hash))
            if len(calls) == 1:
                state.last_analyzed_hash = 123
                state.last_analyzed_at = 100.0
            elif len(calls) == 2:
                worker.update_config(updated)
            else:
                worker.stop()

        with patch("pc_activity_logger.gui.OpenWebUIClient"), patch(
            "pc_activity_logger.gui.run_once", side_effect=capture_cycle
        ):
            worker.run()

        self.assertEqual(calls, [(original, None), (original, 123), (updated, None)])

    def test_saving_equal_configuration_preserves_hash_and_client(self) -> None:
        connection = Mock()
        original = SimpleNamespace(openwebui=connection, capture=SimpleNamespace(interval_sec=0))
        equivalent = SimpleNamespace(openwebui=connection, capture=SimpleNamespace(interval_sec=0))
        worker = ActivityWorker(original)
        hashes = []

        def capture_cycle(config, client, state):
            hashes.append(state.last_analyzed_hash)
            if len(hashes) == 1:
                state.last_analyzed_hash = 123
                worker.update_config(equivalent)
            else:
                worker.stop()

        with patch("pc_activity_logger.gui.OpenWebUIClient") as create_client, patch(
            "pc_activity_logger.gui.run_once", side_effect=capture_cycle
        ):
            worker.run()
        self.assertEqual(hashes, [None, 123])
        create_client.assert_called_once()

    def test_cycle_uses_snapshot_and_next_cycle_uses_latest_change_after_error(self) -> None:
        configs = [SimpleNamespace(openwebui=Mock(), capture=SimpleNamespace(interval_sec=0))
                   for _ in range(3)]
        worker = ActivityWorker(configs[0])
        seen = []

        def capture_cycle(config, client, state):
            seen.append((config, state.last_analyzed_hash))
            if len(seen) == 1:
                worker.update_config(configs[1])
                worker.update_config(configs[2])
                self.assertIs(config, configs[0])
                state.last_analyzed_hash = 123
                raise RuntimeError("synthetic cycle failure")
            worker.stop()

        clients = [Mock(), Mock()]
        with patch("pc_activity_logger.gui.OpenWebUIClient", side_effect=clients), patch(
            "pc_activity_logger.gui.run_once", side_effect=capture_cycle
        ), self.assertLogs("pc_activity_logger", level="ERROR"):
            worker.run()
        self.assertEqual(seen, [(configs[0], None), (configs[2], None)])
        for client in clients:
            client.close.assert_called_once()

    def test_saved_config_applies_when_scheduler_update_fails(self) -> None:
        window = Mock()
        config = Mock()
        window.worker.isRunning.return_value = True
        with patch("pc_activity_logger.gui.save_values", return_value=config), patch(
            "pc_activity_logger.gui.scheduler.is_registered", side_effect=RuntimeError("policy")
        ), patch("pc_activity_logger.gui.QMessageBox") as message:
            result = MainWindow.save_settings(window, show_success=False)
        self.assertIs(result, config)
        window.worker.update_config.assert_called_once_with(config)
        message.critical.assert_called_once()

    def test_failed_save_does_not_update_worker_or_scheduler(self) -> None:
        window = Mock()
        with patch("pc_activity_logger.gui.save_values", side_effect=ValueError("invalid")), patch(
            "pc_activity_logger.gui.scheduler.is_registered"
        ) as scheduler, patch("pc_activity_logger.gui.QMessageBox"):
            result = MainWindow.save_settings(window, show_success=False)
        self.assertIsNone(result)
        window.worker.update_config.assert_not_called()
        scheduler.assert_not_called()

    def test_connection_worker_closes_client_on_success_and_failure(self) -> None:
        for failure in (False, True):
            with self.subTest(failure=failure):
                client = Mock()
                client.list_models.return_value = ["model"]
                if failure:
                    client.list_models.side_effect = RuntimeError("synthetic")
                worker = ConnectionWorker(SimpleNamespace(openwebui=Mock()))
                signals = []
                worker.failed.connect(lambda value: signals.append(("failed", value)))
                worker.succeeded.connect(lambda value: signals.append(("succeeded", value)))
                with patch("pc_activity_logger.gui.OpenWebUIClient", return_value=client):
                    worker.run()
                client.close.assert_called_once()
                self.assertEqual(signals, [("failed", "synthetic")] if failure
                                 else [("succeeded", ["model"])])


if __name__ == "__main__":
    unittest.main()
