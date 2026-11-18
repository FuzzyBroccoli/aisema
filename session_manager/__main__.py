from __future__ import annotations

import argparse
from pathlib import Path

from . import process
from .config import DEFAULT_SESSIONS_FILE, load_config
from .discovery import available_harnesses, discover_recent
from .runner_registry import RunnerPluginError, get_registry
from .storage import load_document
from .tui import run_tui


DEFAULT_SESSION_FILE = DEFAULT_SESSIONS_FILE
DEFAULT_CONFIG_FILE = Path.home() / ".config" / "aisema" / "config"


def main() -> None:
    parser = argparse.ArgumentParser(prog="aisema", description="Aisema: browse and resume sessions")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help=f"session log path (default: {DEFAULT_SESSION_FILE})",
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
        help="discover COUNT newest sessions, or all sessions; omit to use the configured session file only",
    )
    args = parser.parse_args()
    try:
        get_registry()
    except RunnerPluginError as exc:
        parser.error(str(exc))
    config = load_config(args.config)
    session_file = args.file if args.file is not None else config.sessions_file
    document = load_document(session_file)
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
    else:
        active_entries = discover_recent("all", runners=installed_harnesses)
        process.refresh_active(active_entries)
        document.add_discovered([entry for entry in active_entries if entry.active])
    run_tui(document, config)


if __name__ == "__main__":
    main()
