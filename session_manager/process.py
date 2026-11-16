from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable, Optional

from .model import SessionEntry


@dataclass(frozen=True)
class ProcessMatch:
    pid: int
    argv: tuple[str, ...]


def _read_argv(pid: int) -> tuple[str, ...]:
    try:
        data = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return ()
    return tuple(part.decode(errors="replace") for part in data.split(b"\0") if part)


def iter_processes() -> Iterable[ProcessMatch]:
    for child in Path("/proc").iterdir():
        if not child.name.isdigit():
            continue
        argv = _read_argv(int(child.name))
        if argv:
            yield ProcessMatch(int(child.name), argv)


def _terminal_session_tty(session_id: str) -> Optional[str]:
    root = Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.omp/agent")).expanduser()
    terminal_sessions = root / "terminal-sessions"
    try:
        markers = terminal_sessions.iterdir()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for marker in markers:
        if not marker.name.startswith("pts-"):
            continue
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, PermissionError, OSError, UnicodeError):
            continue
        if len(lines) >= 2 and Path(lines[1]).stem.endswith(f"_{session_id}"):
            return f"/dev/pts/{marker.name[4:]}"
    return None


def _process_tty(pid: int) -> Optional[str]:
    for fd in ("0", "1", "2"):
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if target.startswith("/dev/pts/"):
            return target
    return None


def _find_terminal_process(entry: SessionEntry) -> Optional[ProcessMatch]:
    tty = _terminal_session_tty(entry.session_id)
    if tty is None:
        return None
    for process in iter_processes():
        if _process_tty(process.pid) != tty:
            continue
        if any(os.path.basename(argument) == entry.runner for argument in process.argv):
            return process
    return None


def find_process(entry: SessionEntry) -> Optional[ProcessMatch]:
    session_id = entry.session_id
    runner = entry.runner
    if not session_id or not runner:
        return None
    for process in iter_processes():
        joined = " ".join(process.argv)
        if session_id not in joined:
            continue
        if any(os.path.basename(argument) == runner for argument in process.argv):
            return process
    return _find_terminal_process(entry)


def refresh_active(entries: Iterable[SessionEntry]) -> None:
    for entry in entries:
        entry.active = find_process(entry) is not None
