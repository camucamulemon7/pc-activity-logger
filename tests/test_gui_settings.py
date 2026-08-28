import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from pc_activity_logger.gui_settings import default_values, save_values


class GuiSettingsTests(unittest.TestCase):
    def test_saves_api_key_only_to_credential_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.yaml"
            values = default_values()
            values["data_dir"] = str(root / "data")
            values["excluded_app_names"] = ["1Password.exe"]
            values["excluded_window_titles"] = ["機密プロジェクト"]

            with patch("pc_activity_logger.gui_settings.keyring.set_password") as save:
                config = save_values(config_path, values, "super-secret")

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("api_key", raw["openwebui"])
            self.assertNotIn("super-secret", config_path.read_text(encoding="utf-8"))
            self.assertEqual(config.openwebui.api_key, "super-secret")
            self.assertEqual(raw["capture"]["excluded_app_names"], ["1Password.exe"])
            self.assertEqual(
                raw["capture"]["excluded_window_titles"], ["機密プロジェクト"]
            )
            save.assert_called_once_with(
                "PCActivityLogger", "openwebui_api_key", "super-secret"
            )


if __name__ == "__main__":
    unittest.main()
