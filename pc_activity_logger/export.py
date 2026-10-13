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
    parser.add_argument("--strict", action="store_true", help="Stop on corrupt history instead of skipping it")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = export_markdown(args.source, args.vault, strict=args.strict)
    except (OSError, ValueError) as exc:
        LOGGER.error("Markdown export failed: %s", exc)
        return 1
    LOGGER.info("Exported %d records; skipped %d invalid records; destination: %s",
                result.exported, result.skipped, args.vault)
    return 1 if result.skipped and not result.exported else 0


if __name__ == "__main__":
    raise SystemExit(main())
