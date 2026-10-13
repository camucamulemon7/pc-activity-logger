import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from pc_activity_logger.export import main as export_main
from pc_activity_logger.markdown import export_markdown, format_activity_markdown, write_activity_markdown


def activity_record(**changes):
    return {
        "timestamp": "2026-10-13T23:45:00+09:00",
        "activity": "履歴の出力を実装", "project": "pc-activity-logger",
        "category": "development", "app_name": "Code.exe",
        "window_title": "main.py - Visual Studio Code",
        "detail": "日本語の作業履歴を確認\nMarkdownへ変換", "confidence": 0.95,
        "screenshot": "2026-10-13/screenshots/234500_active.jpg",
        "keywords": ["Python", "Obsidian", "ナレッジグラフ"], **changes,
    }


class MarkdownTests(unittest.TestCase):
    def test_one_note_per_record_with_properties_and_resolvable_graph_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, vault = root / "activity.jsonl", root / "vault"
            records = [activity_record(), activity_record(timestamp="2026-10-13T15:00:00Z")]
            original = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
            source.write_text(original, encoding="utf-8-sig")
            self.assertEqual(export_markdown(source, vault), 2)
            notes = list((vault / "PC Activity Logger" / "Records").rglob("*.md"))
            self.assertEqual(len(notes), 2)
            offsets = set()
            for note in notes:
                text = note.read_text(encoding="utf-8")
                properties = yaml.safe_load(text.partition("\n---\n")[0].removeprefix("---\n"))
                offsets.add(properties["timestamp"])
                self.assertIn("keyword/python", properties["tags"])
                self.assertIn("keyword/ナレッジグラフ", properties["tags"])
                self.assertIn("日本語の作業履歴を確認\nMarkdownへ変換", text)
                for link in re.findall(r"\[\[([^|]+)\|[^\]]+\]\]", text):
                    self.assertTrue((vault / link).is_file(), link)
            self.assertEqual(offsets, {record["timestamp"] for record in records})
            self.assertEqual(len(list((vault / "PC Activity Logger" / "Topics").glob("python.md"))), 1)
            self.assertEqual(source.read_text(encoding="utf-8-sig"), original)

    def test_repeat_export_preserves_annotations_and_adds_only_new_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, vault = root / "activity.jsonl", root / "vault"
            source.write_text(json.dumps(activity_record()) + "\n\n", encoding="utf-8")
            export_markdown(source, vault)
            note = next((vault / "PC Activity Logger" / "Records").rglob("*.md"))
            note.write_text(note.read_text(encoding="utf-8") + "\n手動の追記\n", encoding="utf-8")
            with source.open("a", encoding="utf-8") as output:
                output.write(json.dumps(activity_record(activity="追加の記録")) + "\n")
            for _ in range(2):
                self.assertEqual(export_markdown(source, vault), 2)
                self.assertEqual(len(list((vault / "PC Activity Logger" / "Records").rglob("*.md"))), 2)
                self.assertIn("手動の追記", note.read_text(encoding="utf-8"))

    def test_escapes_recorded_text_and_handles_yaml_and_unsafe_keyword_names(self) -> None:
        record = activity_record(activity="確認\n# 偽見出し", project="../../evil: [x]",
            window_title="[link](https://example.com)\n<script>x</script>",
            detail="# heading\n![image](secret)\n<!-- hidden -->\n    **bold** & text",
            keywords=["C++", "C#", "../秘密", "CON", "123", "Ｐｙｔｈｏｎ", "python"])
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault"
            note = write_activity_markdown(record, vault)
            text = note.read_text(encoding="utf-8")
            body = text.partition("\n---\n")[2]
            self.assertIn("確認 \\# 偽見出し", body)
            self.assertIn("&lt;script&gt;x&lt;/script&gt;", body)
            self.assertIn("\\# heading\n\\!\\[image\\]\\(secret\\)", body)
            self.assertNotIn("<script>", body)
            properties = yaml.safe_load(text.partition("\n---\n")[0].removeprefix("---\n"))
            for tag in properties["tags"]:
                self.assertRegex(tag, r"^[\w/-]+$")
            for link in properties["related"]:
                target = link[2:-2].split("|", 1)[0]
                self.assertTrue((vault / target).resolve().is_relative_to(vault.resolve()))
                self.assertTrue((vault / target).is_file())
            topics = list((vault / "PC Activity Logger" / "Topics").glob("*.md"))
            self.assertEqual(len(topics), 6)  # Python width/case variants share a node.

    def test_invalid_record_creates_no_partial_export_and_preserves_existing_notes(self) -> None:
        bad_records = ["{broken}", "[]", json.dumps(activity_record(timestamp="bad")),
            json.dumps(activity_record(detail=None)), json.dumps(activity_record(app_name=[])),
            json.dumps(activity_record(confidence=True)), json.dumps(activity_record(confidence=float("nan"))),
            json.dumps(activity_record(keywords="Python"))]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, vault = root / "activity.jsonl", root / "vault"
            vault.mkdir()
            existing = vault / "personal.md"
            existing.write_text("personal notes", encoding="utf-8")
            for invalid in bad_records:
                with self.subTest(invalid=invalid):
                    original = json.dumps(activity_record()) + "\n" + invalid + "\n"
                    source.write_text(original, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "line 2: invalid activity record"):
                        export_markdown(source, vault)
                    self.assertEqual(source.read_text(encoding="utf-8"), original)
                    self.assertEqual(existing.read_text(encoding="utf-8"), "personal notes")
                    self.assertEqual(list(vault.iterdir()), [existing])

    def test_write_failure_does_not_damage_existing_notes_or_leave_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary)
            existing = vault / "personal.md"
            existing.write_text("personal notes", encoding="utf-8")
            for operation in ("os.replace", "os.fsync"):
                with self.subTest(operation=operation), patch(
                    f"pc_activity_logger.markdown.{operation}", side_effect=OSError("disk failure")
                ), self.assertRaises(OSError):
                    write_activity_markdown(activity_record(), vault)
                self.assertEqual(existing.read_text(encoding="utf-8"), "personal notes")
                self.assertEqual(list(vault.rglob("*.tmp")), [])

    def test_empty_missing_and_invalid_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, vault = root / "activity.jsonl", root / "vault"
            with self.assertRaises(FileNotFoundError):
                export_markdown(source, vault)
            self.assertFalse(vault.exists())
            source.write_text("\n", encoding="utf-8")
            self.assertEqual(export_markdown(source, vault), 0)
            source.write_text(json.dumps(activity_record()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be a directory"):
                export_markdown(source, source)

    def test_old_history_exports_without_image_reanalysis_and_supports_all_days(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for day in ("2026-10-12", "2026-10-13"):
                source = root / "data" / day / "activity.jsonl"
                source.parent.mkdir(parents=True)
                record = activity_record(timestamp=f"{day}T12:00:00+09:00")
                del record["keywords"]
                source.write_text(json.dumps(record), encoding="utf-8")
            vault = root / "vault"
            self.assertEqual(export_markdown(root / "data", vault), 2)
            self.assertEqual(len(list((vault / "PC Activity Logger" / "Records").rglob("*.md"))), 2)
            self.assertFalse((vault / "PC Activity Logger" / "Topics").exists())

    def test_cli_supports_vault_output_and_reports_missing_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, vault = Path(temporary) / "activity.jsonl", Path(temporary) / "vault"
            source.write_text(json.dumps(activity_record()), encoding="utf-8")
            self.assertEqual(export_main([str(source), "--vault", str(vault)]), 0)
            self.assertEqual(len(list((vault / "PC Activity Logger" / "Records").rglob("*.md"))), 1)
            with self.assertLogs("pc_activity_logger", level="ERROR"):
                self.assertEqual(export_main([str(source.parent / "missing.jsonl"), "--vault", str(vault)]), 1)


if __name__ == "__main__":
    unittest.main()
