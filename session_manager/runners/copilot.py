from __future__ import annotations

from pathlib import Path
from ..runner_api import DiscoveredSession, Runner
from ..runner_helpers import generic_json_session

RUNNER_API_VERSION = 1

class CopilotRunner(Runner):
    name = 'copilot'
    command_names = ('copilot',)
    order = 0

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
