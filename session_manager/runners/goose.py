from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

from ..runner_api import (
    DiscoveredSession, NativeFork, Runner, ResumeMatch, SessionContent, SummaryCommand,
    match_option_resume,
)
from ..runner_helpers import timestamp_ns, make_session, generic_json_session
from ..runner_commands import command_help, command_output, has_option

RUNNER_API_VERSION = 1

_RESUME_COMMAND = (
    r"^(?P<command>\S+(?:\s+--\S+)*\s+session\s+resume\s+--name"
    r"(?:\s+|=)(?P<id>\S+)(?:\s+--\S+)*)"
)
_RESUME_RE = re.compile(_RESUME_COMMAND + r"(?= +)")
_RESUME_END_RE = re.compile(_RESUME_COMMAND + r"$")

def _goose_db(path: Path) -> list[DiscoveredSession]:
    try:
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = database.execute(
            "SELECT id, name, description, updated_at FROM sessions WHERE archived_at IS NULL"
        ).fetchall()
        result: list[DiscoveredSession] = []
        fallback = path.stat().st_mtime_ns
        for session_id, name, description, updated in rows:
            if not isinstance(session_id, str):
                continue
            text = str(description or name or "")
            result.append(make_session(RUNNER, path, session_id, text, timestamp_ns(updated, fallback)))
        database.close()
        return result
    except (OSError, sqlite3.Error):
        return []

class GooseRunner(Runner):
    name = "goose"
    command_names = ("goose",)
    order = 5

    def resume_command(self, session_id: str) -> str:
        return f"goose session resume --name {session_id}"

    def session_content(self, session_id: str) -> SessionContent:
        from ..session_context import content_from_text

        help_text = command_help("goose", "session", "export")
        if has_option(help_text, "--format") and has_option(help_text, "--session-id"):
            text = command_output(
                ("goose", "session", "export", "--session-id", session_id, "--format", "json")
            )
            try:
                exported = json.loads(text)
            except ValueError as error:
                raise ValueError("Goose export did not return complete JSON session data") from error
            if not isinstance(exported, dict) or not exported:
                raise ValueError("Goose export returned an empty or invalid session")
            return content_from_text(text)
        # Older installations persisted complete JSONL. A database discovery
        # entry cannot be substituted with its prompt-only description.
        sessions = [item for item in self.discover() if item.session_id == session_id]
        if any(item.path is not None and item.path.suffix == ".jsonl" for item in sessions):
            from ..session_context import load_session_content

            return load_session_content(
                [item for item in sessions if item.path is not None and item.path.suffix == ".jsonl"],
                session_id,
            )
        raise ValueError("This Goose version cannot export full JSON sessions; update Goose to fork with context")

    def native_fork(self, session_id: str) -> NativeFork | None:
        help_text = command_help("goose", "session")
        if all(has_option(help_text, option) for option in ("--resume", "--fork", "--session-id")):
            return NativeFork(("goose", "session", "--resume", "--fork", "--session-id", session_id))
        return None

    def context_command(self, context_file: Path) -> tuple[str, ...] | None:
        help_text = command_help("goose", "run")
        if not all(has_option(help_text, option) for option in ("--interactive", "--instructions")):
            return None
        return ("goose", "run", "--interactive", "--instructions", str(context_file.resolve()))

    def summary_command(self, output_file: Path) -> SummaryCommand:
        help_text = command_help("goose", "run")
        required = ("--instructions", "--no-profile", "--no-session", "--quiet", "--output-format")
        if not all(has_option(help_text, option) for option in required):
            raise ValueError("This Goose version lacks safe fresh JSON summarization; update Goose")
        return SummaryCommand(
            ("goose", "run", "--instructions", "-", "--no-profile", "--no-session",
             "--quiet", "--output-format", "json"),
            output_format="goose-json",
        )

    def match_resume(self, text: str) -> ResumeMatch | None:
        match = _RESUME_RE.match(text) or _RESUME_END_RE.match(text)
        if match:
            return ResumeMatch(match.group("command"), match.group("id"))
        return match_option_resume(text)

    def discover(self) -> list[DiscoveredSession]:
        roots = (Path.home() / ".local" / "share" / "goose" / "sessions", Path.home() / ".config" / "goose" / "sessions")
        result: list[DiscoveredSession] = []
        for root in roots:
            if not root.exists():
                continue
            database = root / "sessions.db"
            if database.is_file():
                result.extend(_goose_db(database))
            for path in root.glob("*.jsonl"):
                if item := generic_json_session(self, path):
                    result.append(item)
        return result

RUNNER = GooseRunner()
