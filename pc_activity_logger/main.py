from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .capture import capture_monitor, crop_to_active_window, difference_hash, hash_distance
from .config import Config, load_config
from .openwebui import OpenWebUIClient
from .storage import append_activity, save_screenshot
from .windows import (
    get_active_window,
    get_idle_seconds,
    is_interactive_session_available,
)


LOGGER = logging.getLogger("pc_activity_logger")


@dataclass
class CaptureState:
    last_analyzed_hash: int | None = None
    last_analyzed_at: float | None = None


def run_once(
    config: Config,
    client: OpenWebUIClient,
    state: CaptureState | None = None,
) -> None:
    if (
        config.capture.skip_unavailable_session
        and not is_interactive_session_available()
    ):
        LOGGER.info("Skipping capture; Windows session is locked or disconnected")
        return

    if config.capture.idle_threshold_sec > 0:
        idle_seconds = get_idle_seconds()
        if idle_seconds >= config.capture.idle_threshold_sec:
            LOGGER.info(
                "Skipping capture; no user input for %.0f seconds (threshold: %d)",
                idle_seconds,
                config.capture.idle_threshold_sec,
            )
            return

    captured_at = datetime.now().astimezone()
    window = get_active_window()
    LOGGER.info("Capturing %s (%s)", window.app_name, window.title)
    image = capture_monitor(window.monitor, config.capture.jpeg_quality)
    analysis_image = crop_to_active_window(
        image,
        window.monitor,
        window.window_rect,
        config.capture.jpeg_quality,
    )
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
            return

    monitor_screenshot_path = save_screenshot(
        config.storage.data_dir, captured_at, image, "monitor"
    )
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
            "monitor_screenshot": str(
                monitor_screenshot_path.relative_to(config.storage.data_dir)
            ),
            **analysis.as_dict(),
        }
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows PC activity logger")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument(
        "--once", action="store_true", help="Capture once and exit instead of looping"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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

    stop = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    LOGGER.info("Started; capture interval is %d seconds", config.capture.interval_sec)
    while not stop:
        started = time.monotonic()
        try:
            run_once(config, client, state)
        except Exception:
            LOGGER.exception("Capture cycle failed")
        remaining = max(0.0, config.capture.interval_sec - (time.monotonic() - started))
        deadline = time.monotonic() + remaining
        while not stop and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    LOGGER.info("Stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
