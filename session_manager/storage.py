from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import tempfile
from typing import List, Union

from .model import SessionEntry, parse_line


@dataclass
class BlankLine:
    raw: str = ""


Line = Union[SessionEntry, BlankLine]


@dataclass
class SessionDocument:
    path: Path
    lines: List[Line]
    had_final_newline: bool
    ephemeral: List[SessionEntry] = field(default_factory=list)

    @property
    def entries(self) -> List[SessionEntry]:
        persisted = [line for line in self.lines if isinstance(line, SessionEntry)]
        return persisted + self.ephemeral

    def add_discovered(self, entries: List[SessionEntry]) -> None:
        known_ids = {
            (entry.runner, entry.session_id)
            for entry in self.entries
            if entry.session_id
        }
        next_line = len(self.lines) + 1
        for entry in entries:
            identity = (entry.runner, entry.session_id)
            if entry.session_id and identity in known_ids:
                continue
            entry.source = "discovered"
            entry.line_number = next_line
            next_line += 1
            self.ephemeral.append(entry)
            if entry.session_id:
                known_ids.add(identity)

    def add_temporary(self, entry: SessionEntry) -> None:
        entry.source = "temporary"
        entry.line_number = len(self.lines) + len(self.ephemeral) + 1
        self.ephemeral.append(entry)

    def persist_entry(self, entry: SessionEntry) -> None:
        if entry.source == "file":
            return
        self.ephemeral = [candidate for candidate in self.ephemeral if candidate is not entry]
        entry.source = "file"
        entry.line_number = len(self.lines) + 1
        self.lines.append(entry)

    def save(self) -> None:
        content_lines = [
            line.serialize() if isinstance(line, SessionEntry) else line.raw
            for line in self.lines
        ]
        content = "\n".join(content_lines)
        if self.had_final_newline and content_lines:
            content += "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        for entry in self.lines:
            if isinstance(entry, SessionEntry):
                entry.raw = entry.serialize()
                entry._dirty = False


def load_document(path: Path) -> SessionDocument:
    path = Path(path).expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    content = path.read_text(encoding="utf-8")
    had_final_newline = content.endswith("\n")
    raw_lines = content.splitlines()
    lines: List[Line] = []
    for line_number, line in enumerate(raw_lines, start=1):
        entry = parse_line(line, line_number)
        lines.append(entry if entry is not None else BlankLine(line))
    return SessionDocument(path, lines, had_final_newline)
