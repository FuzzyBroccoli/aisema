from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import TYPE_CHECKING, Optional

from ..runner_api import DiscoveredSession, Runner
from ..runner_helpers import user_prompt, timestamp_ns, make_session

RUNNER_API_VERSION = 1


if TYPE_CHECKING:
    from ..process import ProcessMatch, ProcessSnapshot

_SESSION_ID_RE = re.compile(r"_([A-Za-z0-9-]+)\.jsonl$")

def _agent_root() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".omp" / "agent"

def _session_root() -> Path:
    return _agent_root() / "sessions"

def _metadata(
    path: Path,
    recap_items: tuple[tuple[int, str, str], ...] = (),
) -> Optional[DiscoveredSession]:
    session_id = ""
    title = ""
    prompt = ""
    prompts: list[str] = []
    inspection_items: list[tuple[int, str, str]] = list(recap_items)
    try:
        updated_ns = path.stat().st_mtime_ns
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record_type = record.get("type")
                if record_type == "title" and isinstance(record.get("title"), str):
                    title = record["title"]
                elif record_type == "session":
                    if isinstance(record.get("id"), str):
                        session_id = record["id"]
                    if not title and isinstance(record.get("title"), str):
                        title = record["title"]
                elif record_type == "message":
                    candidate = user_prompt(record)
                    if candidate:
                        prompt = candidate
                        prompts.append(candidate)
                        timestamp = timestamp_ns(record.get("timestamp"), updated_ns)
                        inspection_items.append((timestamp, "query", candidate))
    except (OSError, UnicodeError):
        return None

    if not session_id:
        match = _SESSION_ID_RE.search(path.name)
        if match:
            session_id = match.group(1)
    if not session_id:
        return None
    return make_session(
        RUNNER,
        path,
        session_id,
        prompt or title or "OMP session",
        updated_ns,
        tuple(prompts),
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )

def _omp_recaps() -> dict[str, tuple[tuple[int, str, str], ...]]:
    path = _agent_root() / "history.db"
    if not path.is_file():
        return {}
    database: Optional[sqlite3.Connection] = None
    try:
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = database.execute(
            "SELECT session_id, recap, created_at "
            "FROM session_recaps ORDER BY created_at, id"
        ).fetchall()
    except (OSError, sqlite3.Error):
        return {}
    finally:
        if database is not None:
            database.close()
    recaps: dict[str, list[tuple[int, str, str]]] = {}
    for session_id, recap, created_at in rows:
        if not isinstance(session_id, str) or not isinstance(recap, str):
            continue
        timestamp = timestamp_ns(created_at, 0)
        recaps.setdefault(session_id, []).append((timestamp, "recap", recap))
    return {session_id: tuple(items) for session_id, items in recaps.items()}

def _terminal_session_markers() -> tuple[tuple[str, str], ...]:
    root = _agent_root()
    terminal_sessions = root / "terminal-sessions"
    try:
        markers = tuple(terminal_sessions.iterdir())
    except (FileNotFoundError, PermissionError, OSError):
        return ()
    found: list[tuple[str, str]] = []
    for marker in markers:
        if not marker.name.startswith("pts-"):
            continue
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, PermissionError, OSError, UnicodeError):
            continue
        if len(lines) >= 2:
            found.append((f"/dev/pts/{marker.name[4:]}", Path(lines[1]).stem))
    return tuple(found)

def _terminal_tty(
    session_id: str,
    markers: tuple[tuple[str, str], ...],
) -> Optional[str]:
    for tty, session_path in markers:
        if session_path.endswith(f"_{session_id}"):
            return tty
    return None

class OmpRunner(Runner):
    name = "omp"
    command_names = ("omp",)
    order = 4

    def find_process(self, session_id: str, snapshot: ProcessSnapshot) -> ProcessMatch | None:
        match = super().find_process(session_id, snapshot)
        if match is not None:
            return match
        if self.name not in snapshot.runner_data:
            snapshot.runner_data[self.name] = _terminal_session_markers()
        tty = _terminal_tty(session_id, snapshot.runner_data[self.name])
        if tty is None:
            return None
        for process in snapshot.by_runner.get(self.name, ()):
            if snapshot.tty_for(process.pid) == tty:
                return process
        return None

    def discover(self) -> list[DiscoveredSession]:
        root = _session_root()
        if not root.exists():
            return []
        recaps = _omp_recaps()
        result: list[DiscoveredSession] = []
        for path in root.rglob("*.jsonl"):
            item = _metadata(path)
            if item is None:
                continue
            inspection_items = tuple(
                sorted(
                    (*item.inspection_items, *recaps.get(item.session_id, ())),
                    key=lambda event: event[0],
                )
            )
            result.append(replace(item, inspection_items=inspection_items))
        return result

RUNNER = OmpRunner()
