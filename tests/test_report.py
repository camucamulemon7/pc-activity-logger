import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import yaml

from pc_activity_logger.config import CaptureConfig, Config, NotesConfig, OpenWebUIConfig, StorageConfig
from pc_activity_logger.main import CaptureState, refresh_daily_report
from pc_activity_logger.report import generate_daily_report, main, render_report, summarize_day


DAY = date(2026, 10, 14)


def record(clock, **changes):
    return {"timestamp": f"2026-10-14T{clock}+09:00", "activity": "同じ作業", "detail": "作業の詳細",
            "project": "project-a", "category": "development", "app_name": "Code.exe",
            "confidence": 0.9, **changes}


class ReportTests(unittest.TestCase):
    def test_aggregates_categories_apps_projects_and_merges_adjacent_same_activity(self) -> None:
        records = [record("09:03:00"), record("09:00:00"), record("09:06:00", activity="資料を調査",
                   app_name="browser.exe", project="project-b", category="research")]
        summary = summarize_day(records, DAY)
        self.assertEqual(summary.total_seconds, 540)
        self.assertEqual(summary.categories, {"development": 360, "research": 180})
        self.assertEqual(summary.apps, {"Code.exe": 360, "browser.exe": 180})
        self.assertEqual(summary.projects, {"project-a": 360, "project-b": 180})
        self.assertEqual(len(summary.timeline), 2)
        self.assertEqual(summary.timeline[0].seconds, 360)
        self.assertIn("66.7%", render_report(summary))

    def test_long_gaps_are_not_counted_as_work_or_merged(self) -> None:
        summary = summarize_day([record("09:00:00"), record("12:00:00")], DAY)
        self.assertEqual(summary.total_seconds, 360)
        self.assertEqual(len(summary.timeline), 2)

    def test_recorded_intervals_short_gaps_and_midnight_are_bounded(self) -> None:
        summary = summarize_day([record("09:00:00", interval_sec=300), record("09:01:00", interval_sec=60),
                                 record("23:59:30", interval_sec=300)], DAY)
        self.assertEqual(summary.total_seconds, 150)
        self.assertEqual(summary.timeline[-1].seconds, 30)
        self.assertEqual(summary.timeline[-1].end.date(), date(2026, 10, 15))
        with self.assertRaises(ValueError):
            summarize_day([], DAY, 0)

    def test_duplicate_timestamps_do_not_double_count_and_other_days_are_excluded(self) -> None:
        summary = summarize_day([record("09:00:00"), record("09:00:00"),
            record("09:00:00", timestamp="2026-10-13T09:00:00+09:00")], DAY)
        self.assertEqual(summary.records, 2)
        self.assertEqual(summary.total_seconds, 180)

    def test_reports_skip_corrupt_rows_resolve_day_link_and_regenerate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, vault = Path(temporary), Path(temporary) / "vault"
            source = root / "data" / DAY.isoformat() / "activity.jsonl"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps(record("09:00:00")) + "\n{broken}\n", encoding="utf-8")
            with self.assertLogs("pc_activity_logger", level="WARNING"):
                result = generate_daily_report(root / "data", DAY, vault)
            self.assertEqual((result.records, result.skipped), (1, 1))
            text = result.path.read_text(encoding="utf-8")
            properties = yaml.safe_load(text.partition("\n---\n")[0].removeprefix("---\n"))
            self.assertEqual(properties["estimated_minutes"], 3)
            target = properties["related"][0][2:-2].split("|", 1)[0]
            self.assertTrue((vault / (target + ".md")).is_file())
            source.write_text(json.dumps(record("09:00:00")) + "\n" + json.dumps(record("09:03:00")), encoding="utf-8")
            updated = generate_daily_report(source, DAY, vault)
            self.assertEqual(updated.total_seconds, 360)
            self.assertIn("推定作業時間: 6.0分", updated.path.read_text(encoding="utf-8"))

    def test_unrelated_notes_and_existing_report_survive_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, vault = Path(temporary) / "activity.jsonl", Path(temporary) / "vault"
            source.write_text(json.dumps(record("09:00:00")), encoding="utf-8")
            result = generate_daily_report(source, DAY, vault)
            original = result.path.read_text(encoding="utf-8")
            with patch("pc_activity_logger.report.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
                generate_daily_report(source, DAY, vault)
            self.assertEqual(result.path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(vault.rglob("*.tmp")), [])
            result.path.write_text("personal note", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unrelated note"):
                generate_daily_report(source, DAY, vault)
            self.assertEqual(result.path.read_text(encoding="utf-8"), "personal note")

    def test_empty_report_and_markdown_table_escaping(self) -> None:
        self.assertIn("記録なし", render_report(summarize_day([], DAY)))
        text = render_report(summarize_day([record("09:00:00", project="a|b\n# title")], DAY))
        self.assertIn("a\\|b \\# title", text)

    def test_cli_generates_without_api_and_missing_history_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / DAY.isoformat() / "activity.jsonl"
            source.parent.mkdir()
            source.write_text(json.dumps(record("09:00:00")), encoding="utf-8")
            self.assertEqual(main(["--data-dir", str(root), "--date", DAY.isoformat()]), 0)
            with self.assertLogs("pc_activity_logger", level="ERROR"):
                self.assertEqual(main(["--data-dir", str(root), "--date", "2026-10-13"]), 1)

    def test_automatic_generation_is_disabled_by_default_and_throttled_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / DAY.isoformat() / "activity.jsonl"
            source.parent.mkdir()
            source.write_text(json.dumps(record("09:00:00")), encoding="utf-8")
            connection = OpenWebUIConfig("http://localhost/api", "synthetic", "model")
            disabled = Config(connection, CaptureConfig(), StorageConfig(root), NotesConfig())
            enabled = Config(connection, CaptureConfig(), StorageConfig(root, reports_enabled=True), NotesConfig())
            state = CaptureState()
            with patch("pc_activity_logger.main.generate_daily_report") as generate, patch(
                "pc_activity_logger.main.time.monotonic", return_value=1000
            ):
                refresh_daily_report(disabled, state, day=DAY)
                generate.assert_not_called()
                refresh_daily_report(enabled, state, day=DAY)
                refresh_daily_report(enabled, state, day=DAY)
                generate.assert_called_once()
                refresh_daily_report(enabled, state, day=DAY, force=True)
                self.assertEqual(generate.call_count, 2)


if __name__ == "__main__":
    unittest.main()
