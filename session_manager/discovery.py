from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterable, Optional

from .model import SessionEntry


SUPPORTED_HARNESSES = ("copilot", "agent", "cursor-agent", "opencode", "omp", "goose", "codex")
_SESSION_ID_RE = re.compile(r"_([A-Za-z0-9-]+)\.jsonl$")
_CODEX_ROLLOUT_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"(?P<thread>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"
    r"(?:_(?P<rollout>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}))?\.jsonl(?:\.zst)?$"
)


@dataclass(frozen=True)
class DiscoveredSession:
    path: Optional[Path]
    session_id: str
    description: str
    updated_ns: int
    runner: str
    resume_command: str
    user_queries: tuple[str, ...] = ()
    inspection_items: tuple[tuple[int, str, str], ...] = ()


def available_harnesses() -> tuple[str, ...]:
    """Return supported harness commands available without starting them."""
    return tuple(runner for runner in SUPPORTED_HARNESSES if shutil.which(runner))


def _agent_root() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".omp" / "agent"


def _session_root() -> Path:
    return _agent_root() / "sessions"


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


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
    if runner == "codex":
        return f"codex resume {session_id}"
    return f"{runner} --resume {session_id}"


def _discovered(
    runner: str,
    path: Optional[Path],
    session_id: str,
    description: str,
    updated_ns: int,
    user_queries: tuple[str, ...] = (),
    inspection_items: tuple[tuple[int, str, str], ...] = (),
) -> DiscoveredSession:
    return DiscoveredSession(
        path=path,
        session_id=session_id,
        description=description or f"{runner} session",
        updated_ns=updated_ns,
        runner=runner,
        resume_command=_resume_command(runner, session_id),
        user_queries=user_queries,
        inspection_items=inspection_items,
    )


def _metadata(
    path: Path,
    recap_items: tuple[tuple[int, str, str], ...] = (),
) -> Optional[DiscoveredSession]:
    session_id = ""
    title = ""
    prompt = ""
    prompts: list[str] = []
    inspection_items: list[tuple[int, str, str]] = list(recap_items)
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
                        timestamp = _timestamp_ns(record.get("timestamp"), updated_ns)
                        inspection_items.append((timestamp, "query", candidate))
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
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )


def _omp_recaps() -> dict[str, tuple[tuple[int, str, str], ...]]:
    path = _agent_root() / "history.db"
    if not path.is_file():
        return {}
    database: Optional[sqlite3.Connection] = None
    try:
        database = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = database.execute(
            "SELECT session_id, recap, created_at "
            "FROM session_recaps ORDER BY created_at, id"
        ).fetchall()
    except (OSError, sqlite3.Error):
        return {}
    finally:
        if database is not None:
            database.close()
    recaps: dict[str, list[tuple[int, str, str]]] = {}
    for session_id, recap, created_at in rows:
        if not isinstance(session_id, str) or not isinstance(recap, str):
            continue
        timestamp = _timestamp_ns(created_at, 0)
        recaps.setdefault(session_id, []).append((timestamp, "recap", recap))
    return {session_id: tuple(items) for session_id, items in recaps.items()}


def _discover_omp() -> list[DiscoveredSession]:
    root = _session_root()
    if not root.exists():
        return []
    recaps = _omp_recaps()
    result: list[DiscoveredSession] = []
    for path in root.rglob("*.jsonl"):
        item = _metadata(path)
        if item is None:
            continue
        inspection_items = tuple(
            sorted(
                (*item.inspection_items, *recaps.get(item.session_id, ())),
                key=lambda event: event[0],
            )
        )
        result.append(replace(item, inspection_items=inspection_items))
    return result


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
    inspection_items: list[tuple[int, str, str]] = []
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
            timestamp = _timestamp_ns(record.get("timestamp"), updated)
            inspection_items.append((timestamp, "query", candidate))
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
    return _discovered(
        runner,
        path,
        record_id,
        prompt or title,
        updated,
        tuple(prompts),
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )


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


def _codex_session(path: Path, fallback_id: str) -> Optional[DiscoveredSession]:
    try:
        updated_ns = path.stat().st_mtime_ns
    except (OSError, ValueError):
        return None
    if path.name.endswith(".zst"):
        return _discovered("codex", path, fallback_id, "Codex session", updated_ns)

    session_id = ""
    prompts: list[str] = []
    inspection_items: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()

    def add_prompt(value: Any, timestamp: int) -> None:
        prompt = _clean_text(value)
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
                timestamp = _timestamp_ns(record.get("timestamp"), updated_ns)
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
    return _discovered(
        "codex",
        path,
        session_id,
        prompts[-1] if prompts else "Codex session",
        updated_ns,
        tuple(prompts),
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )


def _discover_codex() -> list[DiscoveredSession]:
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


def _discover_runner(runner: str) -> list[DiscoveredSession]:
    discoverers = {
        "copilot": _discover_copilot,
        "agent": _discover_agent,
        "cursor-agent": _discover_cursor,
        "opencode": _discover_opencode,
        "omp": _discover_omp,
        "goose": _discover_goose,
        "codex": _discover_codex,
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
                inspection_items=list(item.inspection_items),
            )
        )
    return result
