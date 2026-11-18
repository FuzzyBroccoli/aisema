"""Synthetic runner protocol for isolated integration tests and manual PTY smoke."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import textwrap


PLUGIN_SOURCE = textwrap.dedent('''\
    import json
    from pathlib import Path
    import re
    from session_manager.runner_api import DiscoveredSession, ResumeMatch, Runner

    RUNNER_API_VERSION = 1

    class HarriRunner(Runner):
        name = "harri"
        command_names = ("harri-cli",)

        def new_session_command(self):
            return "harri-cli start"

        def resume_command(self, session_id):
            return f"harri-cli attach {session_id}"

        def match_resume(self, text):
            match = re.match(r"(?P<command>(?:\\S*/)?harri-cli attach (?P<id>[^\\s-]\\S*))(?= |$)", text)
            if match is None:
                return None
            return ResumeMatch(match.group("command"), match.group("id"))

        def discover(self):
            root = Path.home() / ".harri" / "sessions"
            try:
                paths = sorted(root.glob("*.json"))
            except OSError:
                return []
            result = []
            for path in paths:
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    session_id = record["id"]
                    result.append(DiscoveredSession(
                        path=path, session_id=session_id,
                        description=record["description"],
                        updated_ns=record["updated_ns"], runner=self.name,
                        resume_command=self.resume_command(session_id),
                        user_queries=tuple(record["user_queries"]),
                        inspection_items=tuple(tuple(item) for item in record["inspection_items"]),
                    ))
                except (OSError, UnicodeError, ValueError, KeyError, TypeError):
                    continue
            return result

    RUNNER = HarriRunner()
''')

EXECUTABLE_SOURCE = textwrap.dedent('''\
    import json
    from pathlib import Path
    import sys

    root = Path.home() / ".harri"
    sessions = root / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    with (root / "invocations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(args) + "\\n")
    if args == ["start"]:
        session_id = "h-002"
        record = {"id": session_id, "description": "Started fixture",
                  "updated_ns": 4000000000, "user_queries": [], "inspection_items": []}
        (sessions / (session_id + ".json")).write_text(json.dumps(record), encoding="utf-8")
        print("start " + session_id, flush=True)
    elif len(args) == 2 and args[0] == "attach" and (sessions / (args[1] + ".json")).is_file():
        print("attach " + args[1], flush=True)
    else:
        print("invalid fixture arguments", file=sys.stderr)
        sys.exit(2)
    input()
''')

SEED_RECORD = {
    "id": "h-001", "description": "Investigate plugin boundary",
    "updated_ns": 3_000_000_000, "user_queries": ["Check API"],
    "inspection_items": [[1_000_000_000, "recap", "Prior recap"],
                         [2_000_000_000, "query", "Check API"]],
}


@dataclass(frozen=True)
class PluginFixture:
    home: Path
    bin: Path
    plugin_dir: Path
    plugin: Path
    executable: Path
    config: Path
    sessions: Path

    @property
    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home), "PATH": str(self.bin), "TERM": "xterm-256color",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PI_CODING_AGENT_DIR": str(self.home / ".omp" / "agent"),
            "CODEX_HOME": str(self.home / ".codex"),
        }

    @property
    def command(self) -> list[str]:
        return [sys.executable, "-m", "session_manager", "--config", str(self.config),
                "--file", str(self.sessions), "all"]

    def invocations(self) -> list[list[str]]:
        path = self.home / ".harri" / "invocations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def create_fixture(root: Path) -> PluginFixture:
    """Create a disposable HOME, drop-in plugin, executable, config and seed record."""
    root = Path(root).resolve()
    home = root / "home"
    bin_dir = root / "bin"
    plugin_dir = home / ".config" / "aisema" / "runners"
    records = home / ".harri" / "sessions"
    for directory in (bin_dir, plugin_dir, records):
        directory.mkdir(parents=True, exist_ok=True)
    plugin = plugin_dir / "harri.py"
    plugin.write_text(PLUGIN_SOURCE, encoding="utf-8")
    executable = bin_dir / "harri-cli"
    executable.write_text(f"#!{sys.executable}\n" + EXECUTABLE_SOURCE, encoding="utf-8")
    executable.chmod(0o755)
    (records / "h-001.json").write_text(json.dumps(SEED_RECORD), encoding="utf-8")
    sessions = plugin_dir.parent / "sessions.txt"
    sessions.write_text("", encoding="utf-8")
    config = plugin_dir.parent / "config"
    config.write_text(f"sessions_file={sessions}\n", encoding="utf-8")
    return PluginFixture(home, bin_dir, plugin_dir, plugin, executable, config, sessions)
