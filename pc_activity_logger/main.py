from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .capture import UnsafeCaptureError, active_window_crop_box, capture_monitor, crop_to_active_window, difference_hash, hash_distance
from .config import Config, load_config
from .markdown import write_activity_markdown
from .openwebui import OpenWebUIClient
from .report import generate_daily_report
from .storage import append_activity, cleanup_screenshots, save_screenshot
from .windows import (
    get_active_window,
    get_idle_seconds,
    is_interactive_session_available,
    enable_dpi_awareness,
)


LOGGER = logging.getLogger("pc_activity_logger")


@dataclass
class CaptureState:
    last_analyzed_hash: int | None = None
    last_analyzed_at: float | None = None
    maintenance_date: date | None = None
    last_report_at: float | None = None
    last_report_date: date | None = None


@dataclass(frozen=True)
class CycleResult:
    reason: str
    record: dict[str, Any] | None = None


def refresh_daily_report(config: Config, state: CaptureState, *, force: bool = False, day: date | None = None) -> None:
    if not isinstance(config, Config) or not config.storage.reports_enabled:
        return
    report_day = day or datetime.now().astimezone().date()
    now = time.monotonic()
    if (not force and state.last_report_date == report_day and state.last_report_at is not None
            and now - state.last_report_at < 3600):
        return
    source = config.storage.data_dir / report_day.isoformat() / "activity.jsonl"
    if not source.is_file():
        return
    try:
        vault = config.storage.obsidian_vault or config.storage.data_dir / "obsidian"
        generate_daily_report(source, report_day, vault, config.capture.interval_sec)
        state.last_report_at, state.last_report_date = now, report_day
    except (OSError, ValueError, OverflowError):
        LOGGER.exception("Daily report generation failed; recording will continue")


def perform_maintenance(config: Config, state: CaptureState, client: OpenWebUIClient) -> None:
    today = datetime.now().astimezone().date()
    if state.maintenance_date == today:
        return
    try:
        count = cleanup_screenshots(config.storage.data_dir, config.storage.retention_days)
        if count:
            LOGGER.info("Removed %d expired local screenshots", count)
    except OSError:
        LOGGER.exception("Local screenshot cleanup failed; recording will continue")
    if config.openwebui.cleanup_temporary_files:
        try:
            count = client.cleanup_temporary_files()
            if isinstance(count, int) and count:
                LOGGER.info("Removed %d expired OpenWebUI logger images", count)
        except Exception:
            LOGGER.exception("OpenWebUI temporary-file cleanup failed; recording will continue")
    state.maintenance_date = today
    refresh_daily_report(config, state, force=True, day=today - timedelta(days=1))


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    normalized = value.casefold()
    return any(pattern.casefold() in normalized for pattern in patterns)


def is_excluded_window(config: Config, app_name: str, window_title: str) -> bool:
    return _contains_any(app_name, config.capture.excluded_app_names) or _contains_any(
        window_title, config.capture.excluded_window_titles
    )


def run_once(
    config: Config,
    client: OpenWebUIClient,
    state: CaptureState | None = None,
) -> CycleResult:
    if state is not None:
        perform_maintenance(config, state, client)
    if (
        config.capture.skip_unavailable_session
        and not is_interactive_session_available()
    ):
        LOGGER.info("Skipping capture; Windows session is locked or disconnected")
        return CycleResult("unavailable")

    if config.capture.idle_threshold_sec > 0:
        idle_seconds = get_idle_seconds()
        if idle_seconds >= config.capture.idle_threshold_sec:
            LOGGER.info(
                "Skipping capture; no user input for %.0f seconds (threshold: %d)",
                idle_seconds,
                config.capture.idle_threshold_sec,
            )
            return CycleResult("idle")

    window = get_active_window()
    if is_excluded_window(config, window.app_name, window.title):
        LOGGER.info("Skipping capture; foreground window matches an exclusion rule")
        return CycleResult("excluded")

    try:
        crop_box = active_window_crop_box(window.monitor, window.window_rect)
    except UnsafeCaptureError:
        LOGGER.info("Skipping capture; foreground region is too small or unavailable")
        return CycleResult("unsafe")

    captured_at = datetime.now().astimezone()
    LOGGER.info("Capturing %s (%s)", window.app_name, window.title)
    image = None
    if config.storage.save_monitor_screenshot:
        image = capture_monitor(window.monitor, config.capture.jpeg_quality)
        analysis_image = crop_to_active_window(image, window.monitor, window.window_rect, config.capture.jpeg_quality)
    else:
        region = {"left": window.monitor["left"] + crop_box[0],
                  "top": window.monitor["top"] + crop_box[1],
                  "width": crop_box[2] - crop_box[0], "height": crop_box[3] - crop_box[1]}
        analysis_image = capture_monitor(region, config.capture.jpeg_quality)
    current_hash = difference_hash(analysis_image)
    now = time.monotonic()
    if (
        state is not None
        and config.capture.skip_same_screen
        and state.last_analyzed_hash is not None
        and state.last_analyzed_at is not None
    ):
        distance = hash_distance(state.last_analyzed_hash, current_hash)
        elapsed = now - state.last_analyzed_at
        if (
            distance <= config.capture.same_screen_max_distance
            and elapsed < config.capture.same_screen_force_after_sec
        ):
            LOGGER.info(
                "Skipping unchanged screen; hash distance=%d, last analysis %.0f seconds ago",
                distance,
                elapsed,
            )
            return CycleResult("unchanged")

    monitor_screenshot_path = None
    if image is not None:
        monitor_screenshot_path = save_screenshot(config.storage.data_dir, captured_at, image, "monitor")
    screenshot_path = save_screenshot(
        config.storage.data_dir, captured_at, analysis_image, "active"
    )
    file_id = client.upload_temporary_image(analysis_image, captured_at)
    LOGGER.info("Uploaded temporary OpenWebUI File: %s", file_id)
    try:
        try:
            analysis = client.analyze(
                analysis_image, captured_at, window, file_id=file_id
            )
        except Exception:
            LOGGER.error("Analysis failed; local screenshot retained for troubleshooting")
            raise

        record = {
            "timestamp": captured_at.isoformat(),
            "app_name": window.app_name,
            "window_title": window.title,
            "monitor": window.monitor,
            "window_rect": window.window_rect,
            "screenshot": str(screenshot_path.relative_to(config.storage.data_dir)),
            "interval_sec": config.capture.interval_sec,
            **analysis.as_dict(),
        }
        if monitor_screenshot_path is not None:
            record["monitor_screenshot"] = str(monitor_screenshot_path.relative_to(config.storage.data_dir))
        activity_path = append_activity(config.storage.data_dir, captured_at, record)
        if state is not None:
            state.last_analyzed_hash = current_hash
            state.last_analyzed_at = now
    finally:
        try:
            client.delete_file(file_id)
            LOGGER.info("Deleted temporary OpenWebUI File: %s", file_id)
        except Exception:
            LOGGER.exception("Failed to delete temporary OpenWebUI File: %s", file_id)
    if config.storage.markdown_enabled:
        try:
            vault = config.storage.obsidian_vault or config.storage.data_dir / "obsidian"
            write_activity_markdown(record, vault)
        except (OSError, ValueError):
            LOGGER.exception("Markdown export failed; local JSONL record was preserved")
    if config.notes.enabled:
        try:
            note_id = client.append_daily_note(
                captured_at, window, analysis, config.notes.title_prefix
            )
            LOGGER.info("Updated OpenWebUI Note: %s", note_id)
        except Exception:
            LOGGER.exception(
                "OpenWebUI Note update failed; local JSONL record was preserved"
            )
    LOGGER.info(
        "Saved activity to %s: %s (%.2f)",
        activity_path,
        analysis.activity,
        analysis.confidence,
    )
    refresh_daily_report(config, state or CaptureState(), day=captured_at.date())
    return CycleResult("recorded", record)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows PC activity logger")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="Capture once and exit instead of looping"
    )
    mode.add_argument(
        "--background", action="store_true", help="Wait startup_delay_min before the first capture"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    enable_dpi_awareness()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = load_config(args.config)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 2

    client = OpenWebUIClient(config.openwebui)
    state = CaptureState()
    if args.once:
        try:
            run_once(config, client, state)
            return 0
        except Exception:
            LOGGER.exception("Capture cycle failed")
            return 1
        finally:
            client.close()

    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    LOGGER.info("Started; capture interval is %d seconds", config.capture.interval_sec)
    def wait_until(deadline: float) -> None:
        while not stop and time.monotonic() < deadline:
            time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))

    try:
        if args.background and config.capture.startup_delay_min:
            LOGGER.info("Waiting %d minutes before the first capture", config.capture.startup_delay_min)
            wait_until(time.monotonic() + config.capture.startup_delay_min * 60)
        while not stop:
            started = time.monotonic()
            try:
                run_once(config, client, state)
            except Exception:
                LOGGER.exception("Capture cycle failed")
            remaining = max(0.0, config.capture.interval_sec - (time.monotonic() - started))
            wait_until(time.monotonic() + remaining)
    finally:
        refresh_daily_report(config, state, force=True)
        client.close()
    LOGGER.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
