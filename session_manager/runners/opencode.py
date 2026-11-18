from __future__ import annotations

from pathlib import Path
import sqlite3

from ..runner_api import DiscoveredSession, Runner
from ..runner_helpers import timestamp_ns, make_session

RUNNER_API_VERSION = 1

class OpencodeRunner(Runner):
    name = "opencode"
    command_names = ("opencode",)
    order = 3

    def resume_command(self, session_id: str) -> str:
        return f"opencode --session {session_id}"

    def discover(self) -> list[DiscoveredSession]:
        candidates = (
            Path.home() / ".local" / "share" / "opencode" / "opencode.db",
            Path.home() / ".config" / "opencode" / "opencode.db",
        )
        for path in candidates:
            if not path.is_file():
                continue
            try:
                database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                rows = database.execute(
                    "SELECT id, title, time_updated FROM session WHERE parent_id IS NULL"
                ).fetchall()
                result: list[DiscoveredSession] = []
                for session_id, title, updated in rows:
                    if not isinstance(session_id, str):
                        continue
                    result.append(
                        make_session(
                            self,
                            path,
                            session_id,
                            str(title or ""),
                            timestamp_ns(updated, path.stat().st_mtime_ns),
                        )
                    )
                database.close()
                return result
            except (OSError, sqlite3.Error):
                continue
        return []

RUNNER = OpencodeRunner()
