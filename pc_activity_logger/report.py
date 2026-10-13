from __future__ import annotations

import argparse
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .history import read_history, record_timestamp
from .markdown import EXPORT_FOLDER, _entity_path, _frontmatter, _inline_text, _wikilink, _write_new_note

LOGGER = logging.getLogger("pc_activity_logger")
REPORT_MARKER = "<!-- pc-activity-logger:daily-report:v1 -->"


@dataclass
class TimelineEntry:
    start: datetime
    end: datetime
    seconds: float
    activity: str
    app: str
    project: str
    category: str


@dataclass
class DailySummary:
    day: date
    records: int = 0
    skipped: int = 0
    total_seconds: float = 0
    categories: dict[str, float] = field(default_factory=dict)
    apps: dict[str, float] = field(default_factory=dict)
    projects: dict[str, float] = field(default_factory=dict)
    timeline: list[TimelineEntry] = field(default_factory=list)


@dataclass(frozen=True)
class ReportResult:
    path: Path
    records: int
    skipped: int
    total_seconds: float


def summarize_day(records: list[dict[str, Any]], day: date, interval_sec: int = 180) -> DailySummary:
    """Estimate sampled time; cap gaps so idle/sleep is never counted as work."""
    if interval_sec < 1:
        raise ValueError("Report interval must be at least 1 second")
    selected = [record for record in records if record_timestamp(record).date() == day]
    selected.sort(key=lambda record: record_timestamp(record).timestamp())
    summary = DailySummary(day, records=len(selected))
    for index, record in enumerate(selected):
        start = record_timestamp(record)
        end_of_day = datetime.combine(day + timedelta(days=1), time(), tzinfo=start.tzinfo)
        next_time = record_timestamp(selected[index + 1]).timestamp() if index + 1 < len(selected) else end_of_day.timestamp()
        sample_interval = record.get("interval_sec", interval_sec)
        if isinstance(sample_interval, bool) or not isinstance(sample_interval, int) or sample_interval < 1:
            sample_interval = interval_sec
        seconds = max(0.0, min(sample_interval, next_time - start.timestamp(), end_of_day.timestamp() - start.timestamp()))
        if not seconds:
            continue
        category = record.get("category", "").strip() or "unknown"
        app = record.get("app_name", "").strip() or "unknown"
        project = record.get("project", "").strip() or "unknown"
        summary.total_seconds += seconds
        for table, key in ((summary.categories, category), (summary.apps, app), (summary.projects, project)):
            table[key] = table.get(key, 0) + seconds
        entry = TimelineEntry(start, start + timedelta(seconds=seconds), seconds,
                              record["activity"], app, project, category)
        previous = summary.timeline[-1] if summary.timeline else None
        if (previous and previous.end == entry.start and
                (previous.activity, previous.app, previous.project, previous.category) ==
                (entry.activity, entry.app, entry.project, entry.category)):
            previous.end = entry.end
            previous.seconds += seconds
        else:
            summary.timeline.append(entry)
    return summary


def render_report(summary: DailySummary) -> str:
    text = _frontmatter({"type": "pc-activity-report", "date": summary.day.isoformat(),
        "tags": ["pc-activity/report"], "estimated_minutes": round(summary.total_seconds / 60, 2),
        "records": summary.records, "skipped_records": summary.skipped,
        "related": [_wikilink("Days", summary.day.isoformat())]})
    lines = [REPORT_MARKER, "", f"# 作業日報 {summary.day.isoformat()}", "",
        f"- 記録件数: {summary.records}件（破損行のスキップ: {summary.skipped}件）",
        f"- 推定作業時間: {summary.total_seconds / 60:.1f}分", "",
        "定期撮影からの推定値です。各記録から次の記録までを、記録時の撮影間隔を上限として集計します。",
        "最終記録も撮影間隔を上限に推定し、日付をまたぎません。Idle・睡眠・同一画面等の未記録時間は集計せず、実際の勤務時間を表すものではありません。", "",
        f"日付ノート: {_wikilink('Days', summary.day.isoformat())}"]
    for title, table in (("カテゴリ別", summary.categories), ("アプリ別", summary.apps), ("プロジェクト別", summary.projects)):
        lines.extend(["", f"## {title}", "", "| 対象 | 推定時間（分） | 割合 |", "|---|---:|---:|"])
        for name, seconds in sorted(table.items(), key=lambda item: (-item[1], item[0])):
            percent = seconds / summary.total_seconds * 100 if summary.total_seconds else 0
            lines.append(f"| {_inline_text(name)} | {seconds / 60:.1f} | {percent:.1f}% |")
        if not table:
            lines.append("| 記録なし | 0.0 | 0.0% |")
    lines.extend(["", "## タイムライン", "", "| 開始 | 終了 | 推定分 | アプリ | プロジェクト | カテゴリ | 作業 |", "|---|---|---:|---|---|---|---|"])
    for entry in summary.timeline:
        cells = [entry.start.strftime("%H:%M:%S"), entry.end.strftime("%H:%M:%S"), f"{entry.seconds / 60:.1f}",
                 entry.app, entry.project, entry.category, entry.activity]
        lines.append("| " + " | ".join(_inline_text(cell) for cell in cells) + " |")
    if not summary.timeline:
        lines.append("| — | — | 0.0 | — | — | — | 記録なし |")
    return text + "\n".join(lines) + "\n"


def generate_daily_report(source: Path, day: date, vault: Path, interval_sec: int = 180, *, strict: bool = False) -> ReportResult:
    source, vault = Path(source), Path(vault)
    if source.is_dir():
        source = source / day.isoformat() / "activity.jsonl"
    history = read_history(source, strict=strict)
    summary = summarize_day(history.records, day, interval_sec)
    summary.skipped = history.skipped
    path = vault / EXPORT_FOLDER / "Reports" / f"Daily-Report-{day.isoformat()}.md"
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError("Report path must stay inside the Obsidian vault")
    if path.exists() and REPORT_MARKER not in path.read_text(encoding="utf-8"):
        raise ValueError("An unrelated note already exists at the report path")
    content = render_report(summary)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=f".{path.name}.",
                                         suffix=".tmp", dir=path.parent, delete=False) as output:
            temporary_path = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    day_text = _frontmatter({"type": "pc-activity-topic", "aliases": [day.isoformat()], "tags": ["pc-activity/topic"]})
    _write_new_note(vault / _entity_path("Days", day.isoformat()), day_text + f"# {day.isoformat()}\n", vault)
    return ReportResult(path, summary.records, summary.skipped, summary.total_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an estimated daily Markdown activity report")
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now().astimezone().date())
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--interval-sec", type=int, default=180, help="Fallback sampling interval for old records")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = generate_daily_report(args.data_dir, args.date, args.vault or args.data_dir / "obsidian",
                                       args.interval_sec, strict=args.strict)
    except (OSError, ValueError, OverflowError) as exc:
        LOGGER.error("Daily report failed: %s", exc)
        return 1
    LOGGER.info("Daily report: %s; records=%d, skipped=%d, estimated minutes=%.1f",
                result.path, result.records, result.skipped, result.total_seconds / 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
