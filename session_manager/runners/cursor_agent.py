from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import TYPE_CHECKING
from ..runner_api import DiscoveredSession, NativeFork, Runner, SessionContent, SummaryCommand
from ..runner_helpers import generic_json_session
from ..runner_commands import context_instruction

RUNNER_API_VERSION = 1

import re
from ..runner_helpers import clean_text

if TYPE_CHECKING:
    from ..process import ProcessMatch, ProcessSnapshot

def clean_prompt(prompt: str) -> str:
    prompt = re.sub(r"<timestamp>.*?</timestamp>", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"<user_query>(.*?)</user_query>", r"\1", prompt, flags=re.DOTALL)
    return clean_text(prompt)

class CursorAgentRunner(Runner):
    name = 'cursor-agent'
    command_names = ('cursor-agent', 'agent')
    order = 2
    new_session_key = "c"

    def is_available(self) -> bool:
        return any(shutil.which(name) is not None for name in self.command_names)

    def new_session_command(self) -> str:
        return next(
            (name for name in self.command_names if shutil.which(name) is not None),
            self.command_names[0],
        )

    def resume_command(self, session_id: str) -> str:
        return f"{self.new_session_command()} --resume={session_id}"

    def session_content(self, session_id: str) -> SessionContent:
        from ..session_context import content_from_text

        content = super().session_content(session_id)
        if content.cwd is not None:
            return content
        root = Path.home() / ".cursor" / "chats"
        for project in sorted(root.glob("*")):
            metadata = project / session_id / "meta.json"
            try:
                cwd = content_from_text(metadata.read_text(encoding="utf-8")).cwd
            except (OSError, UnicodeError, ValueError):
                continue
            if cwd is not None:
                return SessionContent(content.text, cwd)
        return content

    def native_fork(self, session_id: str) -> NativeFork:
        return NativeFork(
            (self.new_session_command(), "--resume", session_id), instruction="/fork",
        )

    def context_command(self, context_file: Path) -> tuple[str, ...]:
        return (self.new_session_command(), context_instruction(context_file))

    def summary_command(self, output_file: Path) -> SummaryCommand:
        return SummaryCommand(
            (self.new_session_command(), "--print", "--mode=ask", "--output-format", "text")
        )

    def _fd_session_processes(self, snapshot: ProcessSnapshot) -> dict[str, ProcessMatch]:
        root = Path.home() / ".cursor" / "chats"
        found: dict[str, ProcessMatch] = {}
        scanned: set[int] = set()
        for name in self.command_names:
            for process in snapshot.by_runner.get(name, ()):
                if process.pid in scanned:
                    continue
                scanned.add(process.pid)
                try:
                    for fd in (Path("/proc") / str(process.pid) / "fd").iterdir():
                        try:
                            target = os.readlink(fd).removesuffix(" (deleted)")
                            parts = Path(target).relative_to(root).parts
                        except (OSError, ValueError):
                            continue
                        if (
                            len(parts) == 3
                            and ".." not in parts
                            and parts[2] in ("store.db", "store.db-wal", "store.db-shm")
                        ):
                            found.setdefault(parts[1], process)
                except OSError:
                    continue
        return found

    def find_process(self, session_id: str, snapshot: ProcessSnapshot) -> ProcessMatch | None:
        if not session_id:
            return None
        match = super().find_process(session_id, snapshot)
        if match is not None:
            return match
        if self.name not in snapshot.runner_data:
            snapshot.runner_data[self.name] = self._fd_session_processes(snapshot)
        return snapshot.runner_data[self.name].get(session_id)

    def discover(self) -> list[DiscoveredSession]:
        root = Path.home() / ".cursor" / "projects"
        result: list[DiscoveredSession] = []
        if not root.exists():
            return result
        for project in root.iterdir():
            transcript_root = project / "agent-transcripts"
            if not transcript_root.is_dir():
                continue
            for session in transcript_root.iterdir():
                path = session / f"{session.name}.jsonl"
                if path.is_file():
                    item = generic_json_session(self, path, session.name, clean_prompt=clean_prompt)
                    if item:
                        result.append(item)
        return result

RUNNER = CursorAgentRunner()
