import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pc_activity_logger.storage import append_activity, cleanup_screenshots, save_screenshot


class StorageTests(unittest.TestCase):
    def test_retention_only_removes_generated_images_and_keeps_boundary_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = datetime(2026, 10, 14, 12).astimezone()
            for day in ("2026-09-30", "2026-10-01", "2026-10-14", "2026-10-15", "misc"):
                screenshots = root / day / "screenshots"
                screenshots.mkdir(parents=True)
                for name in ("120000_123456_active.jpg", "120000_123456_monitor.jpg", "personal.jpg", "120000_123456_active.jpg.tmp"):
                    (screenshots / name).write_bytes(b"synthetic")
                (root / day / "activity.jsonl").write_text("history")
                (root / day / "note.md").write_text("annotation")
            self.assertEqual(cleanup_screenshots(root, 14, now), 2)
            self.assertFalse((root / "2026-09-30/screenshots/120000_123456_active.jpg").exists())
            for day in ("2026-10-01", "2026-10-14", "2026-10-15", "misc"):
                self.assertTrue((root / day / "screenshots/120000_123456_active.jpg").exists())
            self.assertTrue((root / "2026-09-30/screenshots/personal.jpg").exists())
            self.assertTrue((root / "2026-09-30/activity.jsonl").exists())
            self.assertTrue((root / "2026-09-30/note.md").exists())
            self.assertEqual(cleanup_screenshots(root, 0, now), 0)
            self.assertEqual(cleanup_screenshots(root, -1, now), 0)

    def test_retention_does_not_follow_redirected_screenshot_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            screenshots = root / "2026-01-01/screenshots"
            screenshots.mkdir(parents=True)
            image = screenshots / "120000_123456_active.jpg"
            image.write_bytes(b"synthetic")
            original = Path.resolve
            def redirected(path, *args, **kwargs):
                return root.parent if path == screenshots else original(path, *args, **kwargs)
            with patch.object(Path, "resolve", redirected):
                self.assertEqual(cleanup_screenshots(root, 14), 0)
            self.assertTrue(image.exists())

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
