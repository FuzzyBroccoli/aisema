from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .discovery import available_harnesses, discover_recent
from .storage import load_document
from .tui import run_tui


DEFAULT_SESSION_FILE = Path("..") / "sessions.txt"
DEFAULT_CONFIG_FILE = Path.home() / ".config" / "aisema" / "config"


def main() -> None:
    parser = argparse.ArgumentParser(prog="aisema", description="Aisema: browse and resume sessions")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_SESSION_FILE,
        help="session log path (default: ../sessions.txt)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="configuration path",
    )
    parser.add_argument(
        "session_selector",
        nargs="?",
        metavar="COUNT|ALL",
        help="discover COUNT newest sessions, or all sessions; omit to use sessions.txt only",
    )
    args = parser.parse_args()
    document = load_document(args.file)
    installed_harnesses = available_harnesses()
    if args.session_selector is not None:
        selector = args.session_selector
        if selector.lower() != "all":
            try:
                int(selector)
            except ValueError:
                parser.error("COUNT must be a positive integer or 'all'")
        try:
            document.add_discovered(discover_recent(selector, runners=installed_harnesses))
        except ValueError as exc:
            parser.error(str(exc))
    config = load_config(args.config)
    run_tui(document, config)


if __name__ == "__main__":
    main()
