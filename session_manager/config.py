from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .model import DEFAULT_DATE_FORMAT

DEFAULT_SESSIONS_FILE = Path.home() / ".config" / "session-manager" / "sessions.txt"

DEFAULTS: Dict[str, str] = {
    "sessions_file": str(DEFAULT_SESSIONS_FILE),
    "display_command": "full",
    "show_throwaway": "false",
    "show_done": "true",
    "date_format": DEFAULT_DATE_FORMAT,
    "show_last_used_date": "false",
    "color_runner": "pink",
    "color_hash": "light_gray",
    "color_command": "blue",
    "color_description": "white",
    "color_done": "blue",
    "color_throwaway": "dark_gray",
    "color_selected": "cyan",
    "color_prompt": "yellow",
    "color_error": "red",
}


@dataclass(frozen=True)
class Config:
    values: Dict[str, str]
    path: Path

    def get(self, key: str) -> str:
        return self.values[key]

    @property
    def short_commands(self) -> bool:
        return self.get("display_command").lower() == "short"

    @property
    def hide_commands(self) -> bool:
        return self.get("display_command").lower() == "none"

    @property
    def show_throwaway(self) -> bool:
        return self.get("show_throwaway").lower() in {"1", "true", "yes", "on"}

    @property
    def show_done(self) -> bool:
        return self.get("show_done").lower() in {"1", "true", "yes", "on"}

    @property
    def sessions_file(self) -> Path:
        return Path(self.get("sessions_file")).expanduser()

    @property
    def show_last_used_date(self) -> bool:
        return self.get("show_last_used_date").lower() in {"1", "true", "yes", "on"}


class ConfigError(ValueError):
    pass


def _commented_defaults() -> str:
    lines = [
        "# Aisema configuration",
        "# Values use key=value syntax.",
        "# display_command accepts full, short, or none.",
    ]
    lines.extend(f"# {key}={value}" for key, value in DEFAULTS.items())
    return "\n".join(lines) + "\n"


def load_config(path: Path) -> Config:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_commented_defaults(), encoding="utf-8")
        return Config(dict(DEFAULTS), path)

    original = path.read_text(encoding="utf-8")
    values: Dict[str, str] = {}
    present: set[str] = set()
    for line in original.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        commented = stripped[0] in "#;"
        content = stripped[1:].strip() if commented else stripped
        if "=" not in content:
            continue
        key, value = content.split("=", 1)
        key = key.strip()
        if key not in DEFAULTS:
            continue
        present.add(key)
        if not commented:
            values[key] = value.strip()

    missing = [key for key in DEFAULTS if key not in present]
    if missing:
        addition = "\n".join(f"# {key}={DEFAULTS[key]}" for key in missing) + "\n"
        if original and not original.endswith("\n"):
            original += "\n"
        path.write_text(original + addition, encoding="utf-8")

    merged = dict(DEFAULTS)
    merged.update(values)
    if merged["display_command"].lower() not in {"full", "short", "none"}:
        merged["display_command"] = DEFAULTS["display_command"]
    if merged["show_throwaway"].lower() not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        merged["show_throwaway"] = DEFAULTS["show_throwaway"]
    if merged["show_done"].lower() not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        merged["show_done"] = DEFAULTS["show_done"]
    if not merged["sessions_file"].strip():
        merged["sessions_file"] = DEFAULTS["sessions_file"]
    return Config(merged, path)
