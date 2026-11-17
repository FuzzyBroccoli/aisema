from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Iterable, Optional

from .model import SessionEntry


@dataclass(frozen=True)
class ProcessMatch:
    pid: int
    argv: tuple[str, ...]


@dataclass
class ProcessSnapshot:
    processes: tuple[ProcessMatch, ...]
    by_runner: dict[str, tuple[ProcessMatch, ...]]
    command_lines: dict[int, str]
    terminal_markers: tuple[tuple[str, str], ...]
    process_ttys: dict[int, Optional[str]] = field(default_factory=dict)

    def tty_for(self, pid: int) -> Optional[str]:
        if pid not in self.process_ttys:
            self.process_ttys[pid] = _process_tty(pid)
        return self.process_ttys[pid]


def _read_argv(pid: int) -> tuple[str, ...]:
    try:
        data = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return ()
    return tuple(part.decode(errors="replace") for part in data.split(b"\0") if part)


def iter_processes() -> Iterable[ProcessMatch]:
    current_uid = os.getuid()
    for child in Path("/proc").iterdir():
        if not child.name.isdigit():
            continue
        try:
            if child.stat().st_uid != current_uid:
                continue
        except (FileNotFoundError, PermissionError, OSError):
            continue
        argv = _read_argv(int(child.name))
        if argv:
            yield ProcessMatch(int(child.name), argv)


def _terminal_session_markers() -> tuple[tuple[str, str], ...]:
    root = Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.omp/agent")).expanduser()
    terminal_sessions = root / "terminal-sessions"
    try:
        markers = terminal_sessions.iterdir()
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


def _terminal_session_tty(session_id: str) -> Optional[str]:
    return _terminal_tty(session_id, _terminal_session_markers())


def _process_tty(pid: int) -> Optional[str]:
    for fd in ("0", "1", "2"):
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        if target.startswith("/dev/pts/"):
            return target
    return None


def scan_processes() -> ProcessSnapshot:
    processes = tuple(iter_processes())
    by_runner: dict[str, list[ProcessMatch]] = {}
    command_lines: dict[int, str] = {}
    for process in processes:
        command_lines[process.pid] = " ".join(process.argv)
        for runner in {os.path.basename(argument) for argument in process.argv}:
            by_runner.setdefault(runner, []).append(process)
    return ProcessSnapshot(
        processes=processes,
        by_runner={runner: tuple(matches) for runner, matches in by_runner.items()},
        command_lines=command_lines,
        terminal_markers=_terminal_session_markers(),
    )


def _find_terminal_process(
    entry: SessionEntry,
    snapshot: ProcessSnapshot,
) -> Optional[ProcessMatch]:
    tty = _terminal_tty(entry.session_id, snapshot.terminal_markers)
    if tty is None:
        return None
    for process in snapshot.by_runner.get(entry.runner, ()):
        if snapshot.tty_for(process.pid) == tty:
            return process
    return None


def find_process(
    entry: SessionEntry,
    snapshot: Optional[ProcessSnapshot] = None,
) -> Optional[ProcessMatch]:
    session_id = entry.session_id
    runner = entry.runner
    if not session_id or not runner:
        return None
    current_snapshot = snapshot or scan_processes()
    for process in current_snapshot.by_runner.get(runner, ()):
        if session_id in current_snapshot.command_lines[process.pid]:
            return process
    return _find_terminal_process(entry, current_snapshot)


def refresh_active(
    entries: Iterable[SessionEntry],
    snapshot: Optional[ProcessSnapshot] = None,
) -> None:
    current_snapshot = snapshot or scan_processes()
    for entry in entries:
        entry.active = find_process(entry, current_snapshot) is not None
