from __future__ import annotations

import os
from pathlib import Path
from ..runner_api import DiscoveredSession, NativeFork, Runner, SessionContent, SummaryCommand
from ..runner_helpers import generic_json_session
from ..runner_commands import context_instruction

RUNNER_API_VERSION = 1

class CopilotRunner(Runner):
    name = 'copilot'
    command_names = ('copilot',)
    order = 0

    def session_content(self, session_id: str) -> SessionContent:
        from ..session_context import content_from_text

        configured = os.environ.get("COPILOT_HOME")
        root = Path(configured).expanduser() if configured else Path.home() / ".copilot"
        path = root / "session-state" / session_id / "events.jsonl"
        if path.is_file():
            try:
                return content_from_text(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as error:
                raise ValueError(f"Cannot read complete Copilot events from {path}: {error}") from error
        return super().session_content(session_id)

    def native_fork(self, session_id: str) -> NativeFork:
        return NativeFork(("copilot", "--resume", session_id), instruction="/fork")

    def context_command(self, context_file: Path) -> tuple[str, ...]:
        return ("copilot", "-i", context_instruction(context_file),
                "--attachment", str(context_file.resolve()))

    def summary_command(self, output_file: Path) -> SummaryCommand:
        # Piped stdin is the prompt. -p would ignore it. Denial is permission-
        # level protection, not a sandbox against user-installed hooks/plugins.
        return SummaryCommand(
            ("copilot", "--silent", "--stream=off", "--output-format=text",
             "--no-ask-user", "--no-custom-instructions", "--disable-builtin-mcps",
             "--no-auto-update", "--deny-tool=shell,write,read,url,memory")
        )

    def discover(self) -> list[DiscoveredSession]:
        roots = (Path.home() / ".copilot" / "history-session-state", Path.home() / ".copilot" / "sessions")
        result: list[DiscoveredSession] = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*.json"):
                if item := generic_json_session(self, path):
                    result.append(item)
        return result

RUNNER = CopilotRunner()
