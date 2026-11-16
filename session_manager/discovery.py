from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterable, Optional

from .model import SessionEntry


SUPPORTED_HARNESSES = ("copilot", "agent", "cursor-agent", "opencode", "omp", "goose")
_SESSION_ID_RE = re.compile(r"_([A-Za-z0-9-]+)\.jsonl$")


@dataclass(frozen=True)
class DiscoveredSession:
    path: Optional[Path]
    session_id: str
    description: str
    updated_ns: int
    runner: str
    resume_command: str
    user_queries: tuple[str, ...] = ()

def available_harnesses() -> tuple[str, ...]:
    """Return supported harness commands available without starting them."""
    return tuple(runner for runner in SUPPORTED_HARNESSES if shutil.which(runner))


def _agent_root() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".omp" / "agent"


def _session_root() -> Path:
    return _agent_root() / "sessions"


def _clean_text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, list):
        return " ".join(part for item in value if (part := _clean_text(item)))
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            if key in value:
                text = _clean_text(value[key])
                if text:
                    return text
    return ""


def _user_prompt(record: dict[str, Any]) -> str:
    message = record.get("message")
    role = record.get("role")
    if isinstance(message, dict):
        role = message.get("role", role)
        content = message.get("content")
    else:
        content = record.get("content", record.get("text"))
    if role != "user":
        return ""
    return _clean_text(content)
def _clean_runner_prompt(runner: str, prompt: str) -> str:
    if runner != "cursor-agent":
        return prompt
    prompt = re.sub(r"<timestamp>.*?</timestamp>", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"<user_query>(.*?)</user_query>", r"\1", prompt, flags=re.DOTALL)
    return _clean_text(prompt)



def _timestamp_ns(value: Any, fallback: int) -> int:
    if isinstance(value, (int, float)):
        number = float(value)
        if number < 10**12:
            return int(number * 1_000_000_000)
        if number < 10**15:
            return int(number * 1_000_000)
        if number < 10**18:
            return int(number * 1_000)
        return int(number)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
        return int(parsed.timestamp() * 1_000_000_000)
    return fallback


def _resume_command(runner: str, session_id: str) -> str:
    if runner == "cursor-agent":
        return f"cursor-agent --resume={session_id}"
    if runner == "opencode":
        return f"opencode --session {session_id}"
    if runner == "goose":
        return f"goose session resume --name {session_id}"
    return f"{runner} --resume {session_id}"


def _discovered(
    runner: str,
    path: Optional[Path],
    session_id: str,
    description: str,
    updated_ns: int,
    user_queries: tuple[str, ...] = (),
) -> DiscoveredSession:
    return DiscoveredSession(
        path=path,
        session_id=session_id,
        description=description or f"{runner} session",
        updated_ns=updated_ns,
        runner=runner,
        resume_command=_resume_command(runner, session_id),
        user_queries=user_queries,
    )


def _metadata(path: Path) -> Optional[DiscoveredSession]:
    session_id = ""
    title = ""
    prompt = ""
    prompts: list[str] = []
    try:
        updated_ns = path.stat().st_mtime_ns
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_type = record.get("type")
                if record_type == "title" and isinstance(record.get("title"), str):
                    title = record["title"]
                elif record_type == "session":
                    if isinstance(record.get("id"), str):
                        session_id = record["id"]
                    if not title and isinstance(record.get("title"), str):
                        title = record["title"]
                elif record_type == "message":
                    candidate = _user_prompt(record)
                    if candidate:
                        prompt = candidate
                        prompts.append(candidate)
    except (OSError, UnicodeError):
        return None

    if not session_id:
        match = _SESSION_ID_RE.search(path.name)
        if match:
            session_id = match.group(1)
    if not session_id:
        return None
    return _discovered(
        "omp",
        path,
        session_id,
        prompt or title or "OMP session",
        updated_ns,
        tuple(prompts),
    )


def _discover_omp() -> list[DiscoveredSession]:
    root = _session_root()
    if not root.exists():
        return []
    return [
        item
        for path in root.rglob("*.jsonl")
        if (item := _metadata(path)) is not None
    ]


def _json_records(path: Path) -> Optional[list[dict[str, Any]]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            return [
                record
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for record in [json.loads(line)]
                if isinstance(record, dict)
            ]
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
    except (OSError, UnicodeError):
        return None
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return None
    for key in ("messages", "history", "turns", "records"):
        records = value.get(key)
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
    return [value]


def _generic_json_session(
    runner: str,
    path: Path,
    session_id: Optional[str] = None,
) -> Optional[DiscoveredSession]:
    records = _json_records(path)
    if records is None:
        return None
    prompt = ""
    prompts: list[str] = []
    title = ""
    record_id = session_id or ""
    updated = path.stat().st_mtime_ns
    try:
        top_level = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        top_level = {}
    if isinstance(top_level, dict):
        record_id = record_id or str(top_level.get("sessionId") or top_level.get("id") or "")
        title = str(top_level.get("title") or top_level.get("name") or top_level.get("description") or "")
        for key in ("updatedAt", "lastActivity", "updated_at", "timestamp"):
            if key in top_level:
                updated = _timestamp_ns(top_level[key], updated)
    for record in records:
        candidate = _clean_runner_prompt(runner, _user_prompt(record))
        if candidate:
            prompt = candidate
            prompts.append(candidate)
        for key in ("sessionId", "session_id", "id"):
            if not record_id and isinstance(record.get(key), str):
                record_id = record[key]
        for key in ("title", "name", "description"):
            if not title and isinstance(record.get(key), str):
                title = record[key]
        for key in ("updatedAt", "lastActivity", "updated_at", "timestamp"):
            if key in record:
                updated = _timestamp_ns(record[key], updated)
    if not record_id:
        record_id = path.stem
    return _discovered(runner, path, record_id, prompt or title, updated, tuple(prompts))


def _discover_copilot() -> list[DiscoveredSession]:
    roots = (Path.home() / ".copilot" / "history-session-state", Path.home() / ".copilot" / "sessions")
    result: list[DiscoveredSession] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*.json"):
            if item := _generic_json_session("copilot", path):
                result.append(item)
    return result


def _discover_agent() -> list[DiscoveredSession]:
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
                if item := _generic_json_session("agent", path):
                    result.append(item)
    return result


def _discover_cursor() -> list[DiscoveredSession]:
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
                item = _generic_json_session("cursor-agent", path, session.name)
                if item:
                    result.append(item)
    return result


def _discover_opencode() -> list[DiscoveredSession]:
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
                    _discovered(
                        "opencode",
                        path,
                        session_id,
                        str(title or ""),
                        _timestamp_ns(updated, path.stat().st_mtime_ns),
                    )
                )
            database.close()
            return result
        except (OSError, sqlite3.Error):
            continue
    return []


def _goose_db(path: Path) -> list[DiscoveredSession]:
    try:
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = database.execute(
            "SELECT id, name, description, updated_at FROM sessions WHERE archived_at IS NULL"
        ).fetchall()
        result: list[DiscoveredSession] = []
        fallback = path.stat().st_mtime_ns
        for session_id, name, description, updated in rows:
            if not isinstance(session_id, str):
                continue
            text = str(description or name or "")
            result.append(_discovered("goose", path, session_id, text, _timestamp_ns(updated, fallback)))
        database.close()
        return result
    except (OSError, sqlite3.Error):
        return []


def _discover_goose() -> list[DiscoveredSession]:
    roots = (Path.home() / ".local" / "share" / "goose" / "sessions", Path.home() / ".config" / "goose" / "sessions")
    result: list[DiscoveredSession] = []
    for root in roots:
        if not root.exists():
            continue
        database = root / "sessions.db"
        if database.is_file():
            result.extend(_goose_db(database))
        for path in root.glob("*.jsonl"):
            if item := _generic_json_session("goose", path):
                result.append(item)
    return result


def _discover_runner(runner: str) -> list[DiscoveredSession]:
    discoverers = {
        "copilot": _discover_copilot,
        "agent": _discover_agent,
        "cursor-agent": _discover_cursor,
        "opencode": _discover_opencode,
        "omp": _discover_omp,
        "goose": _discover_goose,
    }
    return discoverers[runner]()


def refresh_last_used(entries: Iterable[SessionEntry]) -> None:
    latest: dict[tuple[str, str], int] = {}
    for runner in available_harnesses():
        for item in _discover_runner(runner):
            key = (item.runner, item.session_id)
            latest[key] = max(latest.get(key, 0), item.updated_ns)
    for entry in entries:
        entry.last_used_ns = latest.get((entry.runner, entry.session_id), 0)


def discover_recent(
    selector: int | str,
    count: Optional[int] = None,
    runners: Optional[Iterable[str]] = None,
) -> list[SessionEntry]:
    """Discover the newest sessions globally, or preserve legacy runner/count use."""
    if count is not None:
        runner = str(selector)
        if runner not in SUPPORTED_HARNESSES:
            raise ValueError(f"unsupported harness: {runner}")
        limit: Optional[int] = count
        selected_runners = (runner,)
    else:
        if isinstance(selector, str) and selector.lower() == "all":
            limit = None
        else:
            try:
                limit = int(selector)
            except (TypeError, ValueError) as exc:
                raise ValueError("session count must be a positive integer") from exc
        selected_runners = tuple(runners) if runners is not None else available_harnesses()
    if limit is not None and limit < 1:
        raise ValueError("session count must be a positive integer")

    metadata: list[DiscoveredSession] = []
    for runner in selected_runners:
        if runner in SUPPORTED_HARNESSES:
            metadata.extend(_discover_runner(runner))
    metadata.sort(key=lambda item: (item.updated_ns, item.runner, item.session_id), reverse=True)

    result: list[SessionEntry] = []
    selected_metadata = metadata if limit is None else metadata[:limit]
    for item in selected_metadata:
        command = item.resume_command
        result.append(
            SessionEntry(
                raw=f"{command} {item.description}",
                command=command,
                separator=" ",
                description=item.description,
                line_number=0,
                source="discovered",
                last_used_ns=item.updated_ns,
                user_queries=list(item.user_queries),
            )
        )
    return result
