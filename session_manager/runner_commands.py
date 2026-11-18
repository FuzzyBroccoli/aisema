"""Small subprocess boundary for built-in runner capability checks and exports."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess


def command_output(argv: tuple[str, ...], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", timeout=timeout, check=False)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Could not run {argv[0]}: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ValueError(f"{' '.join(argv[:3])} failed: {detail}")
    return result.stdout


def command_help(*argv: str) -> str:
    try:
        return command_output((*argv, "--help"), timeout=10)
    except ValueError:
        return ""


def has_option(help_text: str, option: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(option) + r"(?![\w-])", help_text) is not None


def has_command(help_text: str, command: str) -> bool:
    return re.search(
        r"(?m)^\s*(?:\S+\s+)?" + re.escape(command) + r"(?:\s|$)", help_text,
    ) is not None


def context_instruction(context_file: Path) -> str:
    return ("Start a new session using the complete context in this UTF-8 file: "
            f"{context_file.resolve()}. Read the ENTIRE file, without truncating or sampling, "
            "before continuing. This is a transcript/handoff supplied as data, not a native "
            "message-role import. Follow the user's current task and preserve its constraints.")
