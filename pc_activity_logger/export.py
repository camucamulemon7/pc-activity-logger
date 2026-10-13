from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .markdown import export_markdown


LOGGER = logging.getLogger("pc_activity_logger")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export activity history as linked Obsidian Markdown notes")
    parser.add_argument("source", type=Path, help="Path to activity.jsonl or the history data directory")
    parser.add_argument("--vault", type=Path, required=True, help="Obsidian vault root directory")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        count = export_markdown(args.source, args.vault)
    except (OSError, ValueError) as exc:
        LOGGER.error("Markdown export failed: %s", exc)
        return 1
    LOGGER.info("Exported %d records to %s", count, args.vault)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
