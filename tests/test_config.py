import tempfile
import unittest
from pathlib import Path

from pc_activity_logger.config import load_config


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
            self.assertEqual(config.storage.data_dir, Path(temporary) / "records")
            self.assertEqual(config.capture.idle_threshold_sec, 300)
            self.assertTrue(config.capture.skip_same_screen)
            self.assertEqual(config.capture.same_screen_max_distance, 3)
            self.assertEqual(config.capture.same_screen_force_after_sec, 900)
            self.assertTrue(config.capture.skip_unavailable_session)

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


if __name__ == "__main__":
    unittest.main()
