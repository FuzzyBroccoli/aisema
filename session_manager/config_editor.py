from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import tempfile

from .config import DEFAULTS


_CONFIG_LINE_RE = re.compile(
    r"^\s*(?P<comment>[#;])?\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)\s*$"
)


@dataclass
class ConfigLine:
    key: str
    value: str
    active: bool
    raw_index: int

    def render(self) -> str:
        prefix = "" if self.active else "# "
        return f"{prefix}{self.key}={self.value}"


class ConfigEditor:
    """Editable view of known config values; keywords are never editable."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        content = self.path.read_text(encoding="utf-8")
        self.had_final_newline = content.endswith("\n")
        self.raw_lines = content.splitlines()
        self.lines = self._parse_lines()

    def _parse_lines(self) -> list[ConfigLine]:
        found: dict[str, ConfigLine] = {}
        for raw_index, raw in enumerate(self.raw_lines):
            match = _CONFIG_LINE_RE.match(raw)
            if not match or match.group("key") not in DEFAULTS:
                continue
            key = match.group("key")
            item = ConfigLine(
                key=key,
                value=match.group("value"),
                active=match.group("comment") is None,
                raw_index=raw_index,
            )
            previous = found.get(key)
            if previous is None or item.active:
                found[key] = item

        for key, default in DEFAULTS.items():
            if key not in found:
                self.raw_lines.append(f"# {key}={default}")
                found[key] = ConfigLine(key, default, False, len(self.raw_lines) - 1)
        return [found[key] for key in DEFAULTS]

    def toggle_comment(self, index: int) -> ConfigLine:
        item = self.lines[index]
        item.active = not item.active
        self.raw_lines[item.raw_index] = item.render()
        return item

    def toggle_value(self, index: int) -> bool:
        item = self.lines[index]
        pairs = {
            "true": "false",
            "false": "true",
            "on": "off",
            "off": "on",
            "yes": "no",
            "no": "yes",
            "1": "0",
            "0": "1",
            "enabled": "disabled",
            "disabled": "enabled",
            "active": "inactive",
            "inactive": "active",
        }
        replacement = pairs.get(item.value.casefold())
        if replacement is None:
            return False
        item.value = replacement
        self.raw_lines[item.raw_index] = item.render()
        return True

    def set_value(self, index: int, value: str) -> ConfigLine:
        item = self.lines[index]
        item.value = value.strip()
        self.raw_lines[item.raw_index] = item.render()
        return item

    def save(self) -> None:
        content = "\n".join(self.raw_lines)
        if self.had_final_newline and self.raw_lines:
            content += "\n"
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
