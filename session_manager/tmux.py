from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Optional

from .process import ProcessMatch


@dataclass(frozen=True)
class Pane:
    pane_id: str
    pane_pid: int
    window_id: str
    session_id: str

def inside() -> bool:
    return available()




def available() -> bool:
    return bool(os.environ.get("TMUX")) and subprocess.run(
        ["tmux", "-V"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def panes() -> list[Pane]:
    result = subprocess.run(
        ["tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}\t#{window_id}\t#{session_id}"],
        text=True,
        capture_output=True,
        check=False,
    )
    result_list: list[Pane] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 4:
            continue
        try:
            pane_pid = int(fields[1])
        except ValueError:
            continue
        result_list.append(Pane(fields[0], pane_pid, fields[2], fields[3]))
    return result_list


def _parent_pid(pid: int) -> Optional[int]:
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _is_descendant(pid: int, ancestor: int) -> bool:
    seen: set[int] = set()
    current: Optional[int] = pid
    while current and current not in seen:
        if current == ancestor:
            return True
        seen.add(current)
        current = _parent_pid(current)
    return False


def pane_for_process(process: ProcessMatch) -> Optional[Pane]:
    for pane in panes():
        if _is_descendant(process.pid, pane.pane_pid):
            return pane
    return None


def focus(pane: Pane) -> bool:
    commands = [
        ["tmux", "select-window", "-t", pane.window_id],
        ["tmux", "select-pane", "-t", pane.pane_id],
    ]
    for command in commands:
        if subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            return False
    subprocess.run(
        ["tmux", "switch-client", "-t", pane.session_id],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True

def _window_name(description: str) -> str:
    name = description.partition(" ")[0][:16]
    return name or "session"


def launch(command: str, description: str, cwd: Path | None = None) -> Optional[Pane]:
    name = _window_name(description)
    arguments = [
        "tmux",
        "new-window",
        "-d",
        "-P",
        "-F",
        "#{pane_id}\t#{pane_pid}\t#{window_id}\t#{session_id}",
        "-n",
        name,
    ]
    if cwd is not None:
        arguments.extend(["-c", str(cwd)])
    arguments.append(command)
    result = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split("\t")
    if len(fields) != 4:
        return None
    try:
        pane_pid = int(fields[1])
    except ValueError:
        return None
    return Pane(fields[0], pane_pid, fields[2], fields[3])
