"""Read-only session export and Markdown handoff discovery."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable, Iterator
from urllib.parse import unquote

from .runner_api import DiscoveredSession, SessionContent


@dataclass(frozen=True)
class MarkdownFile:
    path: Path
    created: bool


_CWD_KEYS = {"cwd", "directory", "working_dir", "working_directory", "workingdirectory"}
_PATH_KEYS = {"path", "file", "file_path", "filepath", "filename", "file_name", "target", "target_file"}
_WRITE_TOOLS = {"write", "write_file", "create_file", "create", "writefile", "createfile"}
_CREATION_EVENTS = {"file_created", "file_written", "create_file", "write_file"}
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*:(?://)?[^\s<>\"`]+", re.IGNORECASE)
_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\n]*?))\s*\)")
_QUOTED = re.compile(r"`+([^`\n]+)`+|\"([^\"\n]+)\"|'([^'\n]+)'")
_BARE = re.compile(r"(?<![\w./:-])(?:/|\./|\.\./)?[\w@+~.-]+(?:/[\w@+~.-]+)*\.md\b", re.IGNORECASE)
_ADD_FILE = re.compile(r"^\*\*\* Add File: (.+)$", re.MULTILINE)


def _records(text: str, *, strict: bool = False) -> list[object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        records = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                if strict:
                    raise ValueError(f"Invalid session JSON/JSONL at line {number}: {error.msg}") from error
                return [text]
            records.append(value)
    else:
        records = value if isinstance(value, list) else [value]
    if strict and (not records or not all(isinstance(item, (dict, list)) for item in records)):
        raise ValueError("Session source must contain JSON/JSONL objects, not empty or scalar data")
    return records


def _source_cwd(value: object) -> Path | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _CWD_KEYS or key.lower() == "context":
                if isinstance(child, str) and child.strip() and Path(child).is_absolute():
                    return Path(os.path.normpath(child))
        for child in value.values():
            found = _source_cwd(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _source_cwd(child)
            if found is not None:
                return found
    return None


def content_from_text(text: str, cwd: Path | None = None) -> SessionContent:
    """Preserve an export verbatim, taking cwd only from explicit source metadata."""
    if cwd is None:
        cwd = _source_cwd(_records(text))
    return SessionContent(text, cwd)


def load_session_content(discovered: Iterable[DiscoveredSession], session_id: str) -> SessionContent:
    """Load the exact discovered source without resuming or modifying its session."""
    session = next((item for item in discovered if item.session_id == session_id), None)
    if session is None:
        raise ValueError(f"Session {session_id!r} is unavailable; refresh the session list")
    if session.path is None:
        raise ValueError(f"Runner {session.runner!r} must export complete content for session {session_id!r}")
    path = session.path
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError(f"SQLite session {path} requires a complete export from its owning runner ({session.runner})")
    if path.suffix.lower() == ".zst":
        zstd = shutil.which("zstd")
        if zstd is None:
            raise ValueError(f"Install zstd to read compressed session {path}, or export it with {session.runner}")
        result = subprocess.run([zstd, "-d", "-c", "--", str(path)], capture_output=True, check=False)
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Cannot decompress session {path} with zstd: {detail}")
        data = result.stdout
    else:
        data = path.read_bytes()
    if data.startswith(b"SQLite format 3\x00"):
        raise ValueError(f"SQLite session {path} requires a complete export from its owning runner ({session.runner})")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Session {path} is not UTF-8 text; export complete content with {session.runner}") from error
    if "\x00" in text:
        raise ValueError(f"Session {path} contains binary data; export complete content with {session.runner}")
    records = _records(text, strict=True)
    return SessionContent(text, _source_cwd(records))


def _reference(value: str, *, link: bool = False) -> str | None:
    value = value.strip()
    if link:
        # Link destinations may carry a title, fragment or line anchor.
        value = re.sub(r"\s+[\"'].*[\"']$", "", value)
        value = unquote(value.split("#", 1)[0].split("?", 1)[0])
    if not value or value.startswith("//") or _URL.match(value) or "\n" in value or "\x00" in value:
        return None
    value = re.sub(r":\d+(?::\d+)?$", "", value)
    return value if value.lower().endswith(".md") else None


def _text_references(text: str) -> Iterator[str]:
    # Remove recognized spans before bare matching: a URL or a spaced filename
    # must not generate a second, misleading basename-only reference.
    spans: list[tuple[int, int]] = []
    candidates: list[tuple[int, str]] = []
    for match in _ADD_FILE.finditer(text):
        spans.append(match.span())
        reference = _reference(match.group(1))
        if reference:
            candidates.append((match.start(), reference))
    for match in _LINK.finditer(text):
        spans.append(match.span())
        reference = _reference(match.group(1) or match.group(2), link=True)
        if reference:
            candidates.append((match.start(), reference))
    for match in _QUOTED.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        reference = _reference(next(group for group in match.groups() if group is not None))
        if reference:
            spans.append(match.span())
            candidates.append((match.start(), reference))
    spans.extend(match.span() for match in _URL.finditer(text))
    for match in _BARE.finditer(text):
        if not any(start <= match.start() < end for start, end in spans):
            candidates.append((match.start(), match.group()))
    for _, reference in sorted(candidates, key=lambda item: item[0]):
        yield reference


def _path_values(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _PATH_KEYS and isinstance(child, str):
                reference = _reference(child)
                if reference:
                    yield reference
            elif isinstance(child, (dict, list)):
                yield from _path_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _path_values(child)


def _tool_input(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def _created_paths(record: dict) -> Iterator[str]:
    event = str(record.get("type", record.get("event", ""))).lower()
    name = record.get("name", record.get("tool", record.get("tool_name", record.get("toolName", ""))))
    if not isinstance(name, str):
        name = ""
    name = name.lower().rsplit(".", 1)[-1]
    inputs = [record[key] for key in ("arguments", "input", "parameters", "args") if key in record]
    state = record.get("state")
    if isinstance(state, dict) and state.get("status") == "completed" and "input" in state:
        inputs.append(state["input"])
    tool_call = bool(inputs) or any(key in record for key in ("tool", "tool_name", "toolName"))
    action = str(record.get("action", record.get("operation", record.get("command", "")))).lower()
    explicit_write = action in {"create", "created", "write", "written", "create_file", "write_file"}
    if (tool_call and name in _WRITE_TOOLS) or event in _CREATION_EVENTS or explicit_write:
        for value in inputs or [record]:
            yield from _path_values(_tool_input(value))
    if tool_call and name == "apply_patch":
        for value in inputs:
            parsed = _tool_input(value)
            if isinstance(parsed, dict):
                values = (item for item in parsed.values() if isinstance(item, str))
            else:
                values = (parsed,) if isinstance(parsed, str) else ()
            for patch in values:
                for match in _ADD_FILE.finditer(patch):
                    reference = _reference(match.group(1))
                    if reference:
                        yield reference
    # Codex's completed file-change events record additions explicitly.
    if event == "file_change":
        for change in record.get("changes", []):
            if isinstance(change, dict) and change.get("kind") in {"add", "create"}:
                yield from _path_values(change)
    if event == "tool.execution_complete":
        data = record.get("data")
        if isinstance(data, dict):
            for change in data.get("fileEdits", []):
                if isinstance(change, dict) and change.get("kind") == "create":
                    yield from _path_values(change)


def markdown_files(content: SessionContent) -> tuple[MarkdownFile, ...]:
    """List only session-referenced local Markdown files, with written plans first."""
    found: dict[Path, bool] = {}

    def add(reference: str, created: bool = False) -> None:
        path = Path(reference)
        if not path.is_absolute():
            if content.cwd is None or not content.cwd.is_absolute():
                return
            path = content.cwd / path
        path = Path(os.path.normpath(path))
        found[path] = found.get(path, False) or created

    def visit(value: object, creation_allowed: bool = True) -> None:
        if isinstance(value, dict):
            event = str(value.get("type", value.get("event", ""))).lower()
            state = value.get("state")
            if "permission" in event or event == "tool.execution_start":
                creation_allowed = False
            if event == "tool" and isinstance(state, dict) and state.get("status") != "completed":
                creation_allowed = False
            created = tuple(_created_paths(value)) if creation_allowed else ()
            # Preserve the order of original references, upgrading creation
            # evidence afterward even when a path was mentioned earlier.
            for key, child in value.items():
                if key.lower() in _PATH_KEYS and isinstance(child, str):
                    reference = _reference(child)
                    if reference:
                        add(reference)
                elif key in {"arguments", "input", "parameters", "args"} and isinstance(child, str):
                    parsed = _tool_input(child)
                    visit(parsed, creation_allowed)
                else:
                    visit(child, creation_allowed)
            for reference in created:
                add(reference, True)
        elif isinstance(value, list):
            for child in value:
                visit(child, creation_allowed)
        elif isinstance(value, str):
            for reference in _text_references(value):
                add(reference)

    visit(_records(content.text))
    return tuple(
        MarkdownFile(path, created)
        for path, created in sorted(
            found.items(), key=lambda item: not (item[1] and "plan" in item[0].name.lower())
        )
    )
