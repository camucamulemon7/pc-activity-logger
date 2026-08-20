import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pc_activity_logger.storage import append_activity, save_screenshot


class StorageTests(unittest.TestCase):
    def test_saves_screenshot_and_jsonl(self) -> None:
        at = datetime(2026, 8, 20, 12, 34, 56, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = save_screenshot(root, at, b"jpeg", "monitor")
            jsonl_path = append_activity(root, at, {"activity": "test"})
            self.assertEqual(image_path.read_bytes(), b"jpeg")
            self.assertTrue(image_path.name.endswith("_monitor.jpg"))
            record = json.loads(jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(record["activity"], "test")


if __name__ == "__main__":
    unittest.main()
