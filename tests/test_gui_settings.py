import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import yaml

from pc_activity_logger.gui_settings import default_values, read_values, save_values
from pc_activity_logger.prompts import DEFAULT_ANALYSIS_SYSTEM_PROMPT


class GuiSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        credential_read = patch(
            "pc_activity_logger.gui_settings.keyring.get_password", return_value="previous-key"
        )
        self.previous_key = credential_read.start()
        self.addCleanup(credential_read.stop)

    def test_file_replace_failure_restores_previous_credential(self) -> None:
        for previous in ("previous-key", None):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.yaml"
                path.write_text("existing configuration\n", encoding="utf-8")
                self.previous_key.return_value = previous
                with patch("pc_activity_logger.gui_settings.keyring.set_password") as save, patch(
                    "pc_activity_logger.gui_settings.keyring.delete_password"
                ) as delete, patch.object(Path, "replace", side_effect=OSError("synthetic disk failure")):
                    with self.assertRaisesRegex(OSError, "disk failure"):
                        save_values(path, default_values(), "new-synthetic-key")
                expected = [call("PCActivityLogger", "openwebui_api_key", "new-synthetic-key")]
                if previous is not None:
                    expected.append(call("PCActivityLogger", "openwebui_api_key", previous))
                    delete.assert_not_called()
                else:
                    delete.assert_called_once_with("PCActivityLogger", "openwebui_api_key")
                self.assertEqual(save.call_args_list, expected)
                self.assertEqual(path.read_text(encoding="utf-8"), "existing configuration\n")
                self.assertFalse(path.with_name(".config.yaml.tmp").exists())

    def test_credential_rollback_failure_reports_recovery_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text("existing configuration\n", encoding="utf-8")
            with patch("pc_activity_logger.gui_settings.keyring.set_password",
                       side_effect=[None, RuntimeError("synthetic rollback failure")]), patch.object(
                Path, "replace", side_effect=OSError("synthetic disk failure")
            ):
                with self.assertRaisesRegex(RuntimeError, "API Keyの復元にも失敗"):
                    save_values(path, default_values(), "new-synthetic-key")
            self.assertEqual(path.read_text(encoding="utf-8"), "existing configuration\n")
            self.assertFalse(path.with_name(".config.yaml.tmp").exists())

    def test_old_gui_settings_without_prompt_or_exclusions_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "openwebui": {"base_url": "http://localhost/api", "model": "old-model"},
                "storage": {"data_dir": "records"},
                "notes": {"enabled": False},
            }), encoding="utf-8")
            values = read_values(path)
            with patch("pc_activity_logger.gui_settings.keyring.set_password"):
                config = save_values(path, values, "synthetic")
            self.assertEqual(config.openwebui.model, "old-model")
            self.assertEqual(config.openwebui.system_prompt, DEFAULT_ANALYSIS_SYSTEM_PROMPT)
            self.assertEqual(config.capture.excluded_app_names, ())
            self.assertEqual(config.storage.data_dir, Path(temporary).resolve() / "records")
            self.assertFalse(config.notes.enabled)

    def test_invalid_settings_preserve_existing_file_and_do_not_write_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            original = "existing configuration\n"
            path.write_text(original, encoding="utf-8")
            values = default_values()
            values["interval_sec"] = 0
            with patch("pc_activity_logger.gui_settings.keyring.set_password") as save:
                with self.assertRaisesRegex(ValueError, "capture.interval_sec"):
                    save_values(path, values, "synthetic")
            save.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(path.with_name(".config.yaml.tmp").exists())

    def test_credential_store_failure_preserves_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            original = "existing configuration\n"
            path.write_text(original, encoding="utf-8")
            with patch("pc_activity_logger.gui_settings.keyring.set_password",
                       side_effect=RuntimeError("synthetic credential failure")):
                with self.assertRaisesRegex(RuntimeError, "credential failure"):
                    save_values(path, default_values(), "synthetic")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(path.with_name(".config.yaml.tmp").exists())

    def test_saves_api_key_only_to_credential_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.yaml"
            values = default_values()
            values["data_dir"] = str(root / "data")
            values["excluded_app_names"] = ["1Password.exe"]
            values["excluded_window_titles"] = ["機密プロジェクト"]
            values["system_prompt"] = "GUIで変更したシステム指示"

            with patch("pc_activity_logger.gui_settings.keyring.set_password") as save:
                config = save_values(config_path, values, "super-secret")

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("api_key", raw["openwebui"])
            self.assertNotIn("super-secret", config_path.read_text(encoding="utf-8"))
            self.assertEqual(config.openwebui.api_key, "super-secret")
            self.assertEqual(
                raw["openwebui"]["system_prompt"],
                "GUIで変更したシステム指示",
            )
            self.assertEqual(raw["capture"]["excluded_app_names"], ["1Password.exe"])
            self.assertEqual(
                raw["capture"]["excluded_window_titles"], ["機密プロジェクト"]
            )
            save.assert_called_once_with(
                "PCActivityLogger", "openwebui_api_key", "super-secret"
            )


if __name__ == "__main__":
    unittest.main()
