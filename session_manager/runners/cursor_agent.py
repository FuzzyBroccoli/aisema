from __future__ import annotations

from pathlib import Path
from ..runner_api import DiscoveredSession, Runner
from ..runner_helpers import generic_json_session

RUNNER_API_VERSION = 1

import re
from ..runner_helpers import clean_text

def clean_prompt(prompt: str) -> str:
    prompt = re.sub(r"<timestamp>.*?</timestamp>", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"<user_query>(.*?)</user_query>", r"\1", prompt, flags=re.DOTALL)
    return clean_text(prompt)

class CursorAgentRunner(Runner):
    name = 'cursor-agent'
    command_names = ('cursor-agent',)
    order = 2
    hidden_when_available = ("agent",)

    def resume_command(self, session_id: str) -> str:
        return f"cursor-agent --resume={session_id}"

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
