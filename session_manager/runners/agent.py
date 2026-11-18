from __future__ import annotations

from pathlib import Path
from ..runner_api import DiscoveredSession, Runner
from ..runner_helpers import generic_json_session

RUNNER_API_VERSION = 1

class AgentRunner(Runner):
    name = 'agent'
    command_names = ('agent',)
    order = 1

    def discover(self) -> list[DiscoveredSession]:
        roots = (
            Path.home() / ".agent" / "sessions",
            Path.home() / ".agent" / "history",
            Path.home() / ".config" / "agent" / "sessions",
        )
        result: list[DiscoveredSession] = []
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix in {".json", ".jsonl"}:
                    if item := generic_json_session(self, path):
                        result.append(item)
        return result

RUNNER = AgentRunner()
