from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

from ..runner_api import DiscoveredSession, NativeFork, Runner, SessionContent, SummaryCommand
from ..runner_helpers import timestamp_ns, make_session
from ..runner_commands import command_help, command_output, context_instruction, has_command, has_option

RUNNER_API_VERSION = 1

class OpencodeRunner(Runner):
    name = "opencode"
    command_names = ("opencode",)
    order = 3

    def resume_command(self, session_id: str) -> str:
        return f"opencode --session {session_id}"

    def session_content(self, session_id: str) -> SessionContent:
        from ..session_context import content_from_text

        root_help = command_help("opencode")
        if has_command(root_help, "export"):
            argv = ("opencode", "export", session_id)
        elif has_command(root_help, "session") and command_help("opencode", "session", "export"):
            argv = ("opencode", "session", "export", session_id)
        else:
            raise ValueError("This OpenCode version cannot export full JSON sessions; update OpenCode")
        text = command_output(argv)
        try:
            exported = json.loads(text)
        except ValueError as error:
            raise ValueError("OpenCode export did not return complete JSON session data") from error
        if not isinstance(exported, dict) or not exported:
            raise ValueError("OpenCode export returned an empty or invalid session")
        return content_from_text(text)

    def native_fork(self, session_id: str) -> NativeFork | None:
        root_help = command_help("opencode")
        if has_option(root_help, "--fork"):
            return NativeFork(("opencode", "--session", session_id, "--fork"))
        if has_command(root_help, "mini") and has_option(command_help("opencode", "mini"), "--fork"):
            return NativeFork(("opencode", "mini", "--session", session_id, "--fork"))
        return None

    def context_command(self, context_file: Path) -> tuple[str, ...]:
        return ("opencode", "--prompt", context_instruction(context_file))

    def summary_command(self, output_file: Path) -> SummaryCommand:
        help_text = command_help("opencode", "run")
        if not all(has_option(help_text, option) for option in ("--agent", "--format")):
            raise ValueError("This OpenCode version lacks structured fresh summarization; update OpenCode")
        v2 = has_option(help_text, "--standalone")
        directory = output_file.resolve().parent / "opencode-summary"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        agent = {
            "description": "Summarize supplied transcript only",
            "mode": "primary",
        }
        if v2:
            deny = [{"action": "*", "resource": "*", "effect": "deny"}]
            agent.update(system="Summarize only the supplied transcript. Do not use tools.",
                         permissions=deny)
            config = {"agents": {"aisema-summary": agent}, "permissions": deny}
        else:
            agent["permission"] = {"*": "deny"}
            config = {"agent": {"aisema-summary": agent},
                      "permission": {"*": "deny"}, "snapshot": False}
        config_text = json.dumps(config)
        descriptor = os.open(directory / "opencode.json",
                             os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(config_text)
        argv = ("opencode", "run", "--agent", "aisema-summary", "--format", "json")
        if v2:
            argv += ("--standalone",)
        else:
            # V1 inline config has higher precedence than ordinary project config.
            argv = ("env", "OPENCODE_CONFIG_CONTENT=" + config_text, *argv)
        return SummaryCommand(argv, output_format="opencode-json", cwd=directory)

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
