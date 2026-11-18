"""Store normalization helpers shared by built-in and external runners."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable

from .runner_api import DiscoveredSession, Runner


def clean_text(value: Any) -> str:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, list):
        return " ".join(part for item in value if (part := clean_text(item)))
    if isinstance(value, dict):
        for key in ("text", "content", "message", "value"):
            if key in value:
                text = clean_text(value[key])
                if text:
                    return text
    return ""


def user_prompt(record: dict[str, Any]) -> str:
    message = record.get("message")
    role = record.get("role")
    if isinstance(message, dict):
        role = message.get("role", role)
        content = message.get("content")
    else:
        content = record.get("content", record.get("text"))
    if role != "user":
        return ""
    return clean_text(content)


def timestamp_ns(value: Any, fallback: int) -> int:
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


def make_session(
    runner: Runner,
    path: Path | None,
    session_id: str,
    description: str,
    updated_ns: int,
    user_queries: tuple[str, ...] = (),
    inspection_items: tuple[tuple[int, str, str], ...] = (),
) -> DiscoveredSession:
    return DiscoveredSession(
        path=path,
        session_id=session_id,
        description=description or f"{runner.name} session",
        updated_ns=updated_ns,
        runner=runner.name,
        resume_command=runner.resume_command(session_id),
        user_queries=user_queries,
        inspection_items=inspection_items,
    )


def json_records(path: Path) -> list[dict[str, Any]] | None:
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


def generic_json_session(
    runner: Runner,
    path: Path,
    session_id: str | None = None,
    *,
    clean_prompt: Callable[[str], str] | None = None,
) -> DiscoveredSession | None:
    records = json_records(path)
    if records is None:
        return None
    prompt = ""
    prompts: list[str] = []
    inspection_items: list[tuple[int, str, str]] = []
    title = ""
    record_id = session_id or ""
    try:
        updated = path.stat().st_mtime_ns
    except OSError:
        return None
    try:
        top_level = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        top_level = {}
    if isinstance(top_level, dict):
        record_id = record_id or str(top_level.get("sessionId") or top_level.get("id") or "")
        title = str(top_level.get("title") or top_level.get("name") or top_level.get("description") or "")
        for key in ("updatedAt", "lastActivity", "updated_at", "timestamp"):
            if key in top_level:
                updated = timestamp_ns(top_level[key], updated)
    for record in records:
        candidate = user_prompt(record)
        if clean_prompt is not None:
            candidate = clean_prompt(candidate)
        if candidate:
            prompt = candidate
            prompts.append(candidate)
            timestamp = timestamp_ns(record.get("timestamp"), updated)
            inspection_items.append((timestamp, "query", candidate))
        for key in ("sessionId", "session_id", "id"):
            if not record_id and isinstance(record.get(key), str):
                record_id = record[key]
        for key in ("title", "name", "description"):
            if not title and isinstance(record.get(key), str):
                title = record[key]
        for key in ("updatedAt", "lastActivity", "updated_at", "timestamp"):
            if key in record:
                updated = timestamp_ns(record[key], updated)
    if not record_id:
        record_id = path.stem
    return make_session(
        runner, path, record_id, prompt or title, updated, tuple(prompts),
        tuple(sorted(inspection_items, key=lambda item: item[0])),
    )
