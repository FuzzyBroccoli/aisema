from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Iterable, Optional

from .model import SessionEntry
from .runner_api import match_argv_process
from .runner_registry import get_registry


@dataclass(frozen=True)
class ProcessMatch:
    pid: int
    argv: tuple[str, ...]


@dataclass
class ProcessSnapshot:
    processes: tuple[ProcessMatch, ...]
    by_runner: dict[str, tuple[ProcessMatch, ...]]
    command_lines: dict[int, str]
    runner_data: dict[str, object] = field(default_factory=dict)
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
    )


def find_process(
    entry: SessionEntry,
    snapshot: Optional[ProcessSnapshot] = None,
) -> Optional[ProcessMatch]:
    session_id = entry.session_id
    runner = entry.runner
    if not session_id or not runner:
        return None
    current_snapshot = snapshot or scan_processes()
    owner = get_registry().for_command(entry.command)
    if owner is not None:
        return owner.find_process(session_id, current_snapshot)
    return match_argv_process((runner,), session_id, current_snapshot)


def refresh_active(
    entries: Iterable[SessionEntry],
    snapshot: Optional[ProcessSnapshot] = None,
) -> None:
    current_snapshot = snapshot or scan_processes()
    for entry in entries:
        entry.active = find_process(entry, current_snapshot) is not None
