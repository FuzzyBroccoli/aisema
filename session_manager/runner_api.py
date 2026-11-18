"""Dependency-light public API for trusted, synchronous runner plugins."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .process import ProcessMatch, ProcessSnapshot


@dataclass(frozen=True)
class DiscoveredSession:
    path: Path | None
    session_id: str
    description: str
    updated_ns: int
    runner: str
    resume_command: str
    user_queries: tuple[str, ...] = ()
    inspection_items: tuple[tuple[int, str, str], ...] = ()


@dataclass(frozen=True)
class ResumeMatch:
    command: str
    session_id: str


@dataclass(frozen=True)
class SessionContent:
    """Read-only complete session data, not just prompts or active context."""

    text: str
    cwd: Path | None = None


@dataclass(frozen=True)
class NativeFork:
    argv: tuple[str, ...]
    instruction: str = ""


@dataclass(frozen=True)
class SummaryCommand:
    argv: tuple[str, ...]
    output_format: str = "text"
    output_file: Path | None = None
    cwd: Path | None = None


_OPTION_COMMAND = (
    r"^(?P<command>"
    r"(?:\S+(?:\s+--\S+)*\s+--resume(?:\s+|=)\S+(?:\s+--\S+)*"
    r"|\S+(?:\s+--\S+)*\s+--session(?:\s+|=)\S+(?:\s+--\S+)*)"
    r")"
)
_OPTION_RESUME_RE = re.compile(_OPTION_COMMAND + r"(?= +)")
_OPTION_RESUME_END_RE = re.compile(_OPTION_COMMAND + r"$")
_OPTION_ID_RE = re.compile(r"(?:^|\s)(?:--resume|--session)(?:\s+|=)(\S+)")


def match_option_resume(text: str) -> ResumeMatch | None:
    """Recognize the saved-log option grammar, not arbitrary shell syntax."""
    # Prefer the saved-line boundary, even when the description looks like an
    # option. Only use EOF recognition when no separator boundary is possible.
    match = _OPTION_RESUME_RE.match(text) or _OPTION_RESUME_END_RE.match(text)
    if match is None:
        return None
    session_id = _OPTION_ID_RE.search(match.group("command")).group(1)
    return ResumeMatch(match.group("command"), session_id)


def match_argv_process(
    command_names: tuple[str, ...], session_id: str, snapshot: ProcessSnapshot
) -> ProcessMatch | None:
    """Match indexed argv in declared executable and existing process order."""
    if not session_id:
        return None
    for name in command_names:
        for process in snapshot.by_runner.get(name, ()):
            if session_id in snapshot.command_lines[process.pid]:
                return process
    return None


class Runner(ABC):
    """One canonical session identity and its owned executable basenames."""

    name: str
    command_names: tuple[str, ...]
    order: int = 100
    hidden_when_available: tuple[str, ...] = ()

    def is_available(self) -> bool:
        return shutil.which(self.command_names[0]) is not None

    def new_session_command(self) -> str:
        return self.command_names[0]

    def resume_command(self, session_id: str) -> str:
        return f"{self.command_names[0]} --resume {session_id}"

    def match_resume(self, text: str) -> ResumeMatch | None:
        return match_option_resume(text)

    @abstractmethod
    def discover(self) -> list[DiscoveredSession]:
        """Read this runner's store; skip expected missing/malformed records."""

    def find_process(
        self, session_id: str, snapshot: ProcessSnapshot
    ) -> ProcessMatch | None:
        return match_argv_process(self.command_names, session_id, snapshot)

    def session_content(self, session_id: str) -> SessionContent:
        from .session_context import load_session_content

        return load_session_content(self.discover(), session_id)

    def native_fork(self, session_id: str) -> NativeFork | None:
        return None

    def context_command(self, context_file: Path) -> tuple[str, ...] | None:
        """Open a fresh interactive session with a durable context file."""
        return None

    def summary_command(self, output_file: Path) -> SummaryCommand | None:
        """Summarize a fresh stdin prompt without resuming the source."""
        return None
