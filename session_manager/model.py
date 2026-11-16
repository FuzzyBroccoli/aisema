from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
import re
from typing import Optional


DONE_SUFFIX = " Done."
THROWAWAY_SUFFIX = " Throwaway."
DEFAULT_DATE_FORMAT = "%d.%m.%Y"

_ENTRY_RE = re.compile(
    r"^(?P<command>"
    r"(?:\S+(?:\s+--\S+)*\s+--resume\s+\S+(?:\s+--\S+)*"
    r"|\S+(?:\s+--\S+)*\s+--session\s+\S+(?:\s+--\S+)*"
    r"|goose(?:\s+--\S+)*\s+session\s+resume\s+--name\s+\S+(?:\s+--\S+)*)"
    r")(?P<separator> +)(?P<description>.*)$"
)
_ID_RE = re.compile(r"(?:^|\s)(?:--resume|--session|--name)\s+(?P<id>\S+)")


@dataclass
class SessionEntry:
    """One non-empty line from the session log or a discovered session."""

    raw: str
    command: str
    separator: str
    description: str
    line_number: int
    parsed: bool = True
    source: str = "file"
    active: bool = False
    last_used_ns: int = 0
    _dirty: bool = field(default=False, repr=False)

    @property
    def session_id(self) -> str:
        match = _ID_RE.search(self.command)
        return match.group("id") if match else ""

    @property
    def throwaway(self) -> bool:
        return self.description.endswith(THROWAWAY_SUFFIX)

    @property
    def _description_without_throwaway(self) -> str:
        if self.throwaway:
            return self.description[: -len(THROWAWAY_SUFFIX)]
        return self.description

    @property
    def done(self) -> bool:
        return self._description_without_throwaway.endswith(DONE_SUFFIX)

    @property
    def runner(self) -> str:
        if not self.command:
            return ""
        return os.path.basename(self.command.split()[0])

    @property
    def short_session_id(self) -> str:
        session_id = self.session_id
        return session_id.rsplit("-", 1)[-1] if session_id else ""

    @property
    def short_command(self) -> str:
        session_id = self.short_session_id
        return f"{self.runner} {session_id}" if session_id else self.command

    @property
    def discovered(self) -> bool:
        return self.source == "discovered"
    @property
    def temporary(self) -> bool:
        return self.source == "temporary"

    def last_used_text(self, date_format: str = DEFAULT_DATE_FORMAT) -> str:
        if not self.last_used_ns:
            return ""
        try:
            return datetime.fromtimestamp(self.last_used_ns / 1_000_000_000).strftime(date_format)
        except (OverflowError, OSError, ValueError):
            return ""

    def set_description(self, description: str) -> None:
        self.description = description
        self._dirty = True

    def toggle_done(self) -> None:
        if self.throwaway:
            body = self._description_without_throwaway
            if self.done:
                body = body[: -len(DONE_SUFFIX)]
            else:
                body = f"{body}{DONE_SUFFIX}"
            self.description = f"{body}{THROWAWAY_SUFFIX}"
        elif self.done:
            self.description = self.description[: -len(DONE_SUFFIX)]
        else:
            self.description = f"{self.description}{DONE_SUFFIX}"
        self._dirty = True

    def toggle_throwaway(self) -> None:
        if self.throwaway:
            self.description = self._description_without_throwaway
        else:
            self.description = f"{self.description}{THROWAWAY_SUFFIX}"
        self._dirty = True

    def mark_done(self) -> None:
        if not self.done:
            self.toggle_done()

    def mark_undone(self) -> None:
        if self.done:
            self.toggle_done()

    def serialize(self) -> str:
        if not self._dirty:
            return self.raw
        return f"{self.command}{self.separator}{self.description}"


def parse_line(line: str, line_number: int) -> Optional[SessionEntry]:
    if not line.strip():
        return None
    match = _ENTRY_RE.match(line)
    if match:
        return SessionEntry(
            raw=line,
            command=match.group("command"),
            separator=match.group("separator"),
            description=match.group("description"),
            line_number=line_number,
        )
    # Keep malformed/non-standard lines visible and searchable. They cannot be
    # launched safely because no resume command boundary can be identified.
    return SessionEntry(
        raw=line,
        command=line,
        separator="",
        description="",
        line_number=line_number,
        parsed=False,
    )
