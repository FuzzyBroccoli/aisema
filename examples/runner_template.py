"""Copyable, illustrative runner plugin; this is not a real harness schema.

The example executable accepts ``example`` for a new session and
``example --resume <id>`` to resume. Each ~/.example/sessions/*.json file may
contain a record such as:

{
    "id": "example-001",
    "title": "Example investigation",
    "updatedAt": "2026-01-02T03:04:05Z",
    "messages": [
        {
            "role": "user",
            "content": "Inspect the failing import",
            "timestamp": "2026-01-02T03:04:05Z"
        }
    ]
}

The reused helper takes the latest user prompt as the description, retains all
user prompts for inspection, and falls back to the title and file metadata.
Replace these illustrative CLI/storage facts with verified facts for your harness
before installing the plugin in ~/.config/aisema/runners/ and restarting Aisema.
"""

from pathlib import Path

from session_manager.runner_api import DiscoveredSession, Runner
from session_manager.runner_helpers import generic_json_session

RUNNER_API_VERSION = 1


class ExampleRunner(Runner):
    # Canonical identity and every owned executable/wrapper basename. The first
    # executable is used by the default availability, new, and resume methods.
    name = "example"
    command_names = ("example",)

    def discover(self) -> list[DiscoveredSession]:
        # Customize verified storage roots and schema here. Use this helper only
        # when your real records match its documented JSON/JSONL conventions.
        root = Path.home() / ".example" / "sessions"
        sessions: list[DiscoveredSession] = []
        try:
            if not root.is_dir():
                return sessions
            for path in root.glob("*.json"):
                try:
                    session = generic_json_session(self, path)
                except (OSError, UnicodeError):
                    # A record may disappear or become unreadable during a scan.
                    continue
                if session is not None:
                    sessions.append(session)
        except OSError:
            # Missing or unreadable stores are expected discovery conditions.
            return sessions
        return sessions

    # For custom resume syntax, override BOTH resume_command(session_id) and
    # match_resume(text). Return ResumeMatch with the exact command prefix and
    # nonempty ID; do not include the description or its separating spaces.
    # Override new_session_command() only if starting requires extra arguments;
    # its first executable basename must belong to command_names.
    # Inherit find_process() for argv matching. If metadata is needed, override
    # it using the supplied ProcessSnapshot, cache under snapshot.runner_data[
    # self.name], and reuse its indexes/TTY cache instead of rescanning /proc.


RUNNER = ExampleRunner()
