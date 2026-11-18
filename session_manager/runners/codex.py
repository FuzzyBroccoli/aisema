from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Optional

from ..runner_api import DiscoveredSession, Runner, ResumeMatch, match_option_resume
from ..runner_helpers import clean_text, timestamp_ns, make_session

RUNNER_API_VERSION = 1

_CODEX_ROLLOUT_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"(?P<thread>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"
    r"(?:_(?P<rollout>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}))?\.jsonl(?:\.zst)?$"
)

def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"

def _codex_session(path: Path, fallback_id: str) -> Optional[DiscoveredSession]:
    try:
        updated_ns = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return None
    if path.name.endswith(".zst"):
        return make_session(RUNNER, path, fallback_id, "Codex session", updated_ns)

    session_id = ""
    prompts: list[str] = []
    inspection_items: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()

    def add_prompt(value: Any, timestamp: int) -> None:
        prompt = clean_text(value)
        if not prompt or (timestamp, prompt) in seen:
            return
        seen.add((timestamp, prompt))
        prompts.append(prompt)
        inspection_items.append((timestamp, "query", prompt))

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(record, dict):
                    continue
                timestamp = timestamp_ns(record.get("timestamp"), updated_ns)
                record_type = record.get("type")
                payload = record.get("payload")
                if record_type == "session_meta" and isinstance(payload, dict):
                    meta = payload.get("meta")
                    if isinstance(meta, dict) and not session_id:
                        for key in ("id", "session_id"):
                            if isinstance(meta.get(key), str) and meta[key]:
                                session_id = meta[key]
                                break
                if record_type == "event_msg" and isinstance(payload, dict):
                    payload_type = payload.get("type")
                    if payload_type == "user_message":
                        add_prompt(payload.get("message"), timestamp)
                    elif payload_type == "item_completed":
                        item = payload.get("item")
                        if isinstance(item, dict) and item.get("type") == "user_message":
                            add_prompt(item.get("message", item.get("content")), timestamp)
                elif (
                    record_type == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("role") == "user"
                ):
                    add_prompt(payload.get("content"), timestamp)
    except (OSError, UnicodeError):
        return None

    session_id = session_id or fallback_id
    return make_session(
        RUNNER,
        path,
        session_id,
        prompts[-1] if prompts else "Codex session",
        updated_ns,
        tuple(prompts),
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )

class CodexRunner(Runner):
    name = "codex"
    command_names = ("codex",)
    order = 6

    def resume_command(self, session_id: str) -> str:
        return f"codex resume {session_id}"

    def match_resume(self, text: str) -> ResumeMatch | None:
        match = re.match(r"^(?P<command>\S+\s+resume\s+(?!-)(?P<id>\S+))(?= |$)", text)
        if match:
            return ResumeMatch(match.group("command"), match.group("id"))
        return match_option_resume(text)

    def discover(self) -> list[DiscoveredSession]:
        root = _codex_home() / "sessions"
        if not root.exists():
            return []
        candidates: dict[str, DiscoveredSession] = {}
        try:
            for path in root.rglob("rollout-*"):
                if "archived_sessions" in path.parts or not path.is_file():
                    continue
                match = _CODEX_ROLLOUT_RE.fullmatch(path.name)
                if match is None:
                    continue
                item = _codex_session(path, match.group("thread"))
                if item is None:
                    continue
                previous = candidates.get(item.session_id)
                if previous is None or (item.updated_ns, str(path)) > (
                    previous.updated_ns,
                    str(previous.path),
                ):
                    candidates[item.session_id] = item
        except (OSError, UnicodeError):
            return list(candidates.values())
        return list(candidates.values())

RUNNER = CodexRunner()
