import tempfile
import unittest
from pathlib import Path

import yaml

from pc_activity_logger.config import load_config
from pc_activity_logger.prompts import DEFAULT_ANALYSIS_SYSTEM_PROMPT


class ConfigTests(unittest.TestCase):
    def test_resolves_relative_data_dir_from_config_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api/
  api_key: secret
  model: vision-model
storage:
  data_dir: records
""",
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.openwebui.base_url, "http://localhost:8080/api")
            self.assertEqual(config.storage.data_dir, Path(temporary).resolve() / "records")
            self.assertEqual(config.capture.idle_threshold_sec, 300)
            self.assertTrue(config.capture.skip_same_screen)
            self.assertEqual(config.capture.same_screen_max_distance, 3)
            self.assertEqual(config.capture.same_screen_force_after_sec, 900)
            self.assertTrue(config.capture.skip_unavailable_session)
            self.assertEqual(
                config.openwebui.system_prompt, DEFAULT_ANALYSIS_SYSTEM_PROMPT
            )

    def test_rejects_placeholder_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api
  api_key: YOUR_API_KEY
  model: vision-model
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Replace openwebui.api_key"):
                load_config(config_path)

    def test_accepts_api_key_override_when_yaml_omits_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api
  model: vision-model
""",
                encoding="utf-8",
            )
            config = load_config(config_path, api_key_override="credential-secret")
            self.assertEqual(config.openwebui.api_key, "credential-secret")

    def test_loads_window_exclusion_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api
  api_key: secret
  model: vision-model
capture:
  excluded_app_names:
    - 1Password.exe
  excluded_window_titles:
    - Private Project
""",
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.capture.excluded_app_names, ("1Password.exe",))
            self.assertEqual(
                config.capture.excluded_window_titles, ("Private Project",)
            )

    def test_rejects_non_list_window_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api
  api_key: secret
  model: vision-model
capture:
  excluded_app_names: 1Password.exe
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must be a list of strings"):
                load_config(config_path)

    def test_loads_custom_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.yaml"
            config_path.write_text(
                """openwebui:
  base_url: http://localhost:8080/api
  api_key: secret
  model: vision-model
  system_prompt: カスタムシステム指示
""",
                encoding="utf-8",
            )
            config = load_config(config_path)
            self.assertEqual(config.openwebui.system_prompt, "カスタムシステム指示")

    def test_rejects_invalid_integer_settings_with_field_name(self) -> None:
        fields = {
            "openwebui": ("timeout_sec", "max_tokens"),
            "capture": (
                "interval_sec", "jpeg_quality", "idle_threshold_sec",
                "same_screen_max_distance", "same_screen_force_after_sec",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            for section, keys in fields.items():
                for key in keys:
                    for value in (None, True, 1.5, [], {}, "invalid", float("inf")):
                        with self.subTest(section=section, key=key, value=value):
                            raw = {"openwebui": {
                                "base_url": "http://localhost:8080/api",
                                "api_key": "synthetic-key", "model": "model",
                            }}
                            raw.setdefault(section, {})[key] = value
                            path.write_text(yaml.safe_dump(raw), encoding="utf-8")
                            with self.assertRaisesRegex(ValueError, f"{section}.{key}"):
                                load_config(path)

    def test_rejects_invalid_storage_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            for value in (None, True, 123, [], {}, "", "  ", "bad\x00path"):
                with self.subTest(value=value):
                    raw = {"openwebui": {
                        "base_url": "http://localhost:8080/api",
                        "api_key": "synthetic-key", "model": "model",
                    }, "storage": {"data_dir": value}}
                    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "storage.data_dir"):
                        load_config(path)

    def test_accepts_quoted_integers_and_trims_connection_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.yaml"
            path.write_text(yaml.safe_dump({
                "openwebui": {
                    "base_url": " http://localhost:8080/api/ ",
                    "api_key": " synthetic-key ", "model": " model ",
                    "timeout_sec": "120", "max_tokens": "1024",
                },
                "capture": {"interval_sec": "180"},
            }), encoding="utf-8")
            config = load_config(path)
            self.assertEqual(config.capture.interval_sec, 180)
            self.assertEqual(config.openwebui.timeout_sec, 120)
            self.assertEqual(config.openwebui.max_tokens, 1024)
            self.assertEqual(config.openwebui.base_url, "http://localhost:8080/api")
            self.assertEqual(config.openwebui.model, "model")
            self.assertEqual(config.openwebui.api_key, "synthetic-key")

if __name__ == "__main__":
    unittest.main()
