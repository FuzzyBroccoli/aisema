from __future__ import annotations

import curses
from datetime import datetime
import subprocess
import time
from typing import Callable, Optional

from .config import Config, load_config
from .config_editor import ConfigEditor
from .discovery import available_harnesses, discover_recent, refresh_last_used
from .model import SessionEntry
from . import process, tmux
from .storage import SessionDocument


_COLOR_VALUES = {
    "black": curses.COLOR_BLACK,
    "red": curses.COLOR_RED,
    "green": curses.COLOR_GREEN,
    "yellow": curses.COLOR_YELLOW,
    "blue": curses.COLOR_BLUE,
    "magenta": curses.COLOR_MAGENTA,
    "cyan": curses.COLOR_CYAN,
    "white": curses.COLOR_WHITE,
    "default": -1,
}

_PALETTE_NAMES = (
    "default",
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "light_gray",
    "dark_gray",
    "pink",
    "bright_black",
    "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
    "bright_white",
)

FIRST_SORT_MODES = ("status", "none", "date", "command")
SECOND_SORT_MODES = ("date", "none", "original", "id", "command", "description", "active")


def _color_value(name: str) -> tuple[int, int]:
    normalized = name.strip().lower()
    bold = 0
    if normalized == "pink":
        normalized = "magenta"
        bold = curses.A_BOLD
    elif normalized in {"light_gray", "dark_gray"}:
        normalized = "black"
        bold = curses.A_BOLD
    elif normalized.startswith("bright_"):
        normalized = normalized[7:]
        bold = curses.A_BOLD
    return _COLOR_VALUES.get(normalized, -1), bold


def _safe_addstr(stdscr: curses.window, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if width <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, width, attr)
    except curses.error:
        pass

def _status_marker(entry: SessionEntry) -> str:
    return "".join(
        (
            "A" if entry.active else " ",
            "D" if entry.done else " ",
            "X" if entry.throwaway else " ",
            "N" if entry.source != "file" else " ",
        )
    )


def _word_left(buffer: list[str], cursor: int) -> int:
    while cursor and buffer[cursor - 1].isspace():
        cursor -= 1
    while cursor and not buffer[cursor - 1].isspace():
        cursor -= 1
    return cursor


def _word_right(buffer: list[str], cursor: int) -> int:
    length = len(buffer)
    while cursor < length and buffer[cursor].isspace():
        cursor += 1
    while cursor < length and not buffer[cursor].isspace():
        cursor += 1
    return cursor


def _read_escape_sequence(stdscr: curses.window) -> str:
    parts: list[str] = []
    stdscr.timeout(50)
    try:
        for _ in range(8):
            key = stdscr.getch()
            if key == -1:
                break
            parts.append(chr(key))
            if key != ord("[") and 0x40 <= key <= 0x7E:
                break
    finally:
        stdscr.timeout(250)
    return "".join(parts)


def _sort_entries(
    entries: list[SessionEntry],
    mode: str,
    descending: bool,
    first_mode: str = "status",
) -> list[SessionEntry]:
    mode = mode.lower()
    first_mode = first_mode.lower()
    if mode == "status":
        mode = "date"
    if mode == "none":
        key: Callable[[SessionEntry], object] = lambda entry: 0
    elif mode == "original":
        key = lambda entry: entry.line_number
    elif mode == "id":
        key = lambda entry: entry.session_id.casefold()
    elif mode == "command":
        key = lambda entry: entry.command.casefold()
    elif mode == "description":
        key = lambda entry: entry.description.casefold()
    elif mode == "active":
        key = lambda entry: not entry.active
    elif mode == "date":
        key = lambda entry: entry.last_used_ns
    else:
        raise ValueError("sort must be date, none, original, id, command, description, or active")

    if first_mode == "status":
        groups = [[] for _ in range(4)]
        for entry in entries:
            if entry.throwaway:
                index = 3
            elif entry.active:
                index = 0
            elif entry.done:
                index = 2
            else:
                index = 1
            groups[index].append(entry)
    elif first_mode == "none":
        groups = [list(entries)]
    elif first_mode == "date":
        dated: dict[int, list[SessionEntry]] = {}
        for entry in entries:
            if not entry.last_used_ns:
                date_key = -1
            else:
                try:
                    date_key = datetime.fromtimestamp(entry.last_used_ns / 1_000_000_000).date().toordinal()
                except (OverflowError, OSError, ValueError):
                    date_key = -1
            dated.setdefault(date_key, []).append(entry)
        groups = [dated[key] for key in sorted(dated, reverse=True)]
    elif first_mode == "command":
        commanded: dict[str, list[SessionEntry]] = {}
        for entry in entries:
            commanded.setdefault(entry.runner.casefold(), []).append(entry)
        groups = [commanded[key] for key in sorted(commanded)]
    else:
        raise ValueError("first sort must be status, none, date, or command")

    for group in groups:
        if mode == "date":
            group.sort(key=key, reverse=True)
        else:
            group.sort(key=key, reverse=descending)
    return [entry for group in groups for entry in group]




def _read_line(stdscr: curses.window, prompt: str, initial: str = "") -> Optional[str]:
    buffer = list(initial)
    cursor = len(buffer)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    stdscr.keypad(False)

    def finish(result: Optional[str]) -> Optional[str]:
        stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        return result
    while True:
        height, width = stdscr.getmaxyx()
        y = max(0, height - 1)
        available = max(1, width - len(prompt) - 1)
        start = max(0, cursor - available)
        visible = "".join(buffer[start : start + available])
        stdscr.move(y, 0)
        _safe_addstr(stdscr, y, 0, " " * max(1, width - 1), max(1, width - 1))
        _safe_addstr(stdscr, y, 0, prompt + visible, max(1, width - 1), curses.A_BOLD)
        try:
            stdscr.move(y, min(width - 1, len(prompt) + cursor - start))
        except curses.error:
            pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (10, 13, curses.KEY_ENTER):
            return finish("".join(buffer))
        if key == 27:
            sequence = _read_escape_sequence(stdscr)
            if sequence.endswith("D"):
                cursor = _word_left(buffer, cursor) if "5" in sequence else max(0, cursor - 1)
                continue
            if sequence.endswith("C"):
                cursor = _word_right(buffer, cursor) if "5" in sequence else min(len(buffer), cursor + 1)
                continue
            if sequence in {"[H", "[1~"}:
                cursor = 0
                continue
            if sequence in {"[F", "[4~"}:
                cursor = len(buffer)
                continue
            if sequence == "[3~":
                if cursor < len(buffer):
                    del buffer[cursor]
                continue
            return finish(None)
        if key in (curses.KEY_BACKSPACE, 8, 127):
            if cursor:
                del buffer[cursor - 1]
                cursor -= 1
        elif key == curses.KEY_DC:
            if cursor < len(buffer):
                del buffer[cursor]
        elif key == getattr(curses, "KEY_SLEFT", -999):
            cursor = _word_left(buffer, cursor)
        elif key == getattr(curses, "KEY_SRIGHT", -999):
            cursor = _word_right(buffer, cursor)
        elif key == curses.KEY_LEFT:
            cursor = max(0, cursor - 1)
        elif key == curses.KEY_RIGHT:
            cursor = min(len(buffer), cursor + 1)
        elif key == curses.KEY_HOME or key == 1:
            cursor = 0
        elif key == curses.KEY_END or key == 5:
            cursor = len(buffer)
        elif key == 21:
            buffer.clear()
            cursor = 0
        elif 0 <= key <= 255 and chr(key).isprintable():
            buffer.insert(cursor, chr(key))
            cursor += 1


class SessionApp:
    def __init__(self, stdscr: curses.window, document: SessionDocument, config: Config) -> None:
        self.stdscr = stdscr
        self.document = document
        self.config = config
        self.first_sort_mode = "status"
        self.sort_mode = "date"
        self.sort_descending = False
        self.query = ""
        self.visible: list[SessionEntry] = []
        self.selected = 0
        self.new_session_baselines: dict[int, set[str]] = {}
        self.new_session_description_edits: set[int] = set()
        self.new_session_panes: dict[int, tmux.Pane] = {}
        self.scroll_top = 0
        self.throwaway_visible: set[int] = set()
        self.help_visible = False
        self.config_editor: Optional[ConfigEditor] = None
        self.config_selected = 0
        self.message = ""
        self.message_is_error = False
        self.last_active_refresh = 0.0
        self.show_last_used_date = config.show_last_used_date
        self.color_pairs: dict[str, int] = {}
        self._setup_colors()
        if self.show_last_used_date:
            refresh_last_used(self.document.entries)
        self.rebuild()

    def _setup_colors(self) -> None:
        try:
            curses.start_color()
            curses.use_default_colors()
        except curses.error:
            return
        for number, role in enumerate(
            ("active", "runner", "hash", "command", "description", "done", "throwaway", "selected", "prompt", "error"),
            start=1,
        ):
            color, bold = _color_value(self.config.get(f"color_{role}"))
            try:
                curses.init_pair(number, color, -1)
                self.color_pairs[role] = curses.color_pair(number) | bold
            except curses.error:
                self.color_pairs[role] = 0

    def _palette_attr(self, color_name: str, index: int, selected: bool = False) -> int:
        color, bold = _color_value(color_name)
        attr = bold
        try:
            pair_number = 20 + index
            curses.init_pair(pair_number, color, -1)
            attr |= curses.color_pair(pair_number)
        except curses.error:
            pass
        if selected:
            attr |= curses.A_REVERSE
        return attr

    def _attr(self, role: str, selected: bool = False) -> int:
        attr = self.color_pairs.get(role, 0)
        if selected:
            if role != "active":
                attr = self.color_pairs.get("selected", attr)
            attr |= curses.A_REVERSE
        return attr

    def rebuild(self, selected_entry: Optional[SessionEntry] = None) -> None:
        entries = _sort_entries(
            self.document.entries,
            self.sort_mode,
            self.sort_descending,
            getattr(self, "first_sort_mode", "status"),
        )
        if not self.config.show_done:
            entries = [entry for entry in entries if not entry.done]
        if not self.config.show_throwaway:
            entries = [
                entry
                for entry in entries
                if not entry.throwaway or id(entry) in self.throwaway_visible
            ]
        if self.query:
            query = self.query.casefold()
            entries = [
                entry
                for entry in entries
                if query in entry.description.casefold()
                or query in entry.command.casefold()
                or query in entry.session_id.casefold()
            ]
        self.visible = entries
        if selected_entry is not None and selected_entry in entries:
            self.selected = entries.index(selected_entry)
        elif self.visible:
            self.selected = min(self.selected, len(self.visible) - 1)
        else:
            self.selected = 0
        self._keep_selected_visible()

    def _keep_selected_visible(self) -> None:
        height, _ = self.stdscr.getmaxyx()
        list_height = max(1, height - 3)
        if self.selected < self.scroll_top:
            self.scroll_top = self.selected
        elif self.selected >= self.scroll_top + list_height:
            self.scroll_top = self.selected - list_height + 1

    def _move_selection(self, target: int) -> None:
        if not self.visible:
            self.selected = 0
        else:
            self.selected = max(0, min(target, len(self.visible) - 1))
        self._keep_selected_visible()

    def _set_message(self, message: str, error: bool = False) -> None:
        self.message = message
        self.message_is_error = error

    def _save(
        self,
        entry: SessionEntry,
        rebuild: bool = True,
        materialize: bool = True,
    ) -> bool:
        if not materialize:
            if rebuild:
                self.rebuild(entry)
            return True
        self.document.persist_entry(entry)
        try:
            self.document.save()
        except OSError as exc:
            self._set_message(f"save failed: {exc}", True)
            return False
        if rebuild:
            self.rebuild(entry)
        return True

    def _reload_config(self) -> None:
        self.config = load_config(self.config.path)
        self.show_last_used_date = self.config.show_last_used_date
        if self.show_last_used_date:
            refresh_last_used(self.document.entries)
        self._setup_colors()
        self.rebuild(self._selected_entry())

    def _save_config_editor(self) -> bool:
        if self.config_editor is None:
            return False
        try:
            self.config_editor.save()
            self._reload_config()
        except OSError as exc:
            self._set_message(f"config save failed: {exc}", True)
            return False
        return True

    def _config_options(self, index: int) -> Optional[list[str]]:
        assert self.config_editor is not None
        key = self.config_editor.lines[index].key
        if key == "display_command":
            return ["full", "short", "none"]
        if key.startswith("color_"):
            return list(_PALETTE_NAMES)
        return None

    def _pick_config_value(self) -> None:
        assert self.config_editor is not None
        options = self._config_options(self.config_selected)
        if not options:
            self._set_message("no picker is available for this value")
            return
        item = self.config_editor.lines[self.config_selected]
        current = item.value.casefold()
        selected = next((index for index, value in enumerate(options) if value == current), 0)
        height, width = self.stdscr.getmaxyx()
        popup_height = min(len(options) + 2, max(3, height - 2))
        popup_width = min(max(len(option) for option in options) + 8, max(12, width - 2))
        top = max(0, (height - popup_height) // 2)
        left = max(0, (width - popup_width) // 2)
        try:
            popup = curses.newwin(popup_height, popup_width, top, left)
        except curses.error:
            self._set_message("terminal is too small for the picker", True)
            return
        popup.keypad(True)
        first = 0
        try:
            while True:
                visible_count = max(1, popup_height - 2)
                if selected < first:
                    first = selected
                elif selected >= first + visible_count:
                    first = selected - visible_count + 1
                popup.erase()
                popup.box()
                _safe_addstr(popup, 0, 2, f" {item.key} ", max(1, popup_width - 4), curses.A_BOLD)
                for offset, option in enumerate(options[first : first + visible_count]):
                    option_index = first + offset
                    if item.key.startswith("color_"):
                        attr = self._palette_attr(option, option_index, option_index == selected)
                    else:
                        attr = curses.A_REVERSE if option_index == selected else 0
                    _safe_addstr(popup, 1 + offset, 2, option, max(1, popup_width - 4), attr)
                popup.refresh()
                key = popup.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return
                if key == curses.KEY_UP:
                    selected = max(0, selected - 1)
                elif key == curses.KEY_DOWN:
                    selected = min(len(options) - 1, selected + 1)
                elif key == curses.KEY_PPAGE:
                    selected = max(0, selected - visible_count)
                elif key == curses.KEY_NPAGE:
                    selected = min(len(options) - 1, selected + visible_count)
                elif key in (10, 13, curses.KEY_ENTER):
                    self.config_editor.set_value(self.config_selected, options[selected])
                    if self._save_config_editor():
                        self._set_message(f"{item.key} updated")
                    return
        finally:
            del popup

    def _new_session_harnesses(self) -> list[str]:
        installed = list(available_harnesses())
        installed_set = set(installed)
        ordered: list[str] = []
        for entry in [*self.visible, *self.document.entries]:
            runner = entry.runner
            if runner in installed_set and runner not in ordered:
                ordered.append(runner)
        ordered.extend(runner for runner in installed if runner not in ordered)
        return ordered

    def _choose_harness(self, harnesses: list[str]) -> Optional[str]:
        height, width = self.stdscr.getmaxyx()
        popup_height = min(len(harnesses) + 2, max(3, height - 2))
        popup_width = min(max(max(map(len, harnesses)) + 8, 20), max(20, width - 2))
        top = max(0, (height - popup_height) // 2)
        left = max(0, (width - popup_width) // 2)
        try:
            popup = curses.newwin(popup_height, popup_width, top, left)
        except curses.error:
            self._set_message("terminal is too small for the harness picker", True)
            return None
        popup.keypad(True)
        selected = 0
        first = 0
        try:
            while True:
                visible_count = max(1, popup_height - 2)
                if selected < first:
                    first = selected
                elif selected >= first + visible_count:
                    first = selected - visible_count + 1
                popup.erase()
                popup.box()
                _safe_addstr(popup, 0, 2, " New session ", max(1, popup_width - 4), curses.A_BOLD)
                for offset, harness in enumerate(harnesses[first : first + visible_count]):
                    index = first + offset
                    attr = curses.A_REVERSE if index == selected else 0
                    _safe_addstr(popup, 1 + offset, 2, harness, max(1, popup_width - 4), attr)
                popup.refresh()
                key = popup.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return None
                if key == curses.KEY_UP:
                    selected = max(0, selected - 1)
                elif key == curses.KEY_DOWN:
                    selected = min(len(harnesses) - 1, selected + 1)
                elif key == curses.KEY_PPAGE:
                    selected = max(0, selected - visible_count)
                elif key == curses.KEY_NPAGE:
                    selected = min(len(harnesses) - 1, selected + visible_count)
                elif key in (10, 13, curses.KEY_ENTER):
                    return harnesses[selected]
        finally:
            del popup

    def _choose_sort_mode(self) -> Optional[tuple[str, str]]:
        layer = "second"
        options = list(SECOND_SORT_MODES)
        height, width = self.stdscr.getmaxyx()
        popup_height = min(max(len(FIRST_SORT_MODES), len(SECOND_SORT_MODES)) + 2, max(3, height - 2))
        popup_width = min(max(max(map(len, SECOND_SORT_MODES)) + 8, 20), max(20, width - 2))
        top = max(0, (height - popup_height) // 2)
        left = max(0, (width - popup_width) // 2)
        try:
            popup = curses.newwin(popup_height, popup_width, top, left)
        except curses.error:
            self._set_message("terminal is too small for the sort picker", True)
            return None
        popup.keypad(True)
        selected = options.index(self.sort_mode) if self.sort_mode in options else 0
        first = 0
        try:
            while True:
                visible_count = max(1, popup_height - 2)
                if selected < first:
                    first = selected
                elif selected >= first + visible_count:
                    first = selected - visible_count + 1
                popup.erase()
                popup.box()
                title = f" Sort {layer} layer "
                _safe_addstr(popup, 0, 2, title, max(1, popup_width - 4), curses.A_BOLD)
                for offset, option in enumerate(options[first : first + visible_count]):
                    index = first + offset
                    attr = curses.A_REVERSE if index == selected else 0
                    _safe_addstr(popup, 1 + offset, 2, option, max(1, popup_width - 4), attr)
                _safe_addstr(
                    popup,
                    popup_height - 1,
                    2,
                    "S: other layer",
                    max(1, popup_width - 4),
                    curses.A_DIM,
                )
                popup.refresh()
                key = popup.getch()
                if key in (ord("q"), ord("Q"), 27):
                    return None
                if key in (ord("s"), ord("S")):
                    layer = "first" if layer == "second" else "second"
                    options = list(FIRST_SORT_MODES if layer == "first" else SECOND_SORT_MODES)
                    current = self.first_sort_mode if layer == "first" else self.sort_mode
                    selected = options.index(current) if current in options else 0
                    first = 0
                elif key == curses.KEY_UP:
                    selected = max(0, selected - 1)
                elif key == curses.KEY_DOWN:
                    selected = min(len(options) - 1, selected + 1)
                elif key == curses.KEY_PPAGE:
                    selected = max(0, selected - visible_count)
                elif key == curses.KEY_NPAGE:
                    selected = min(len(options) - 1, selected + visible_count)
                elif key in (10, 13, curses.KEY_ENTER):
                    return layer, options[selected]
        finally:
            del popup

    def _remember_new_session(self, entry: SessionEntry) -> None:
        known = {
            candidate.session_id
            for candidate in self.document.entries
            if candidate.runner == entry.runner and candidate.session_id
        }
        try:
            known.update(
                candidate.session_id
                for candidate in discover_recent("all", runners=(entry.runner,))
                if candidate.session_id
            )
        except (OSError, ValueError):
            pass
        self.new_session_baselines = getattr(self, "new_session_baselines", {})
        self.new_session_baselines[id(entry)] = known

    def _refresh_new_sessions(self) -> None:
        baselines = getattr(self, "new_session_baselines", {})
        description_edits = getattr(self, "new_session_description_edits", set())
        for entry in list(self.document.entries):
            known = baselines.get(id(entry))
            if known is None or entry.session_id:
                continue
            try:
                candidates = discover_recent("all", runners=(entry.runner,))
            except (OSError, ValueError):
                continue
            candidate = next(
                (item for item in candidates if item.session_id and item.session_id not in known),
                None,
            )
            if candidate is None:
                continue
            entry.command = candidate.command
            entry.separator = " "
            entry.last_used_ns = candidate.last_used_ns
            entry._dirty = True
            known.add(candidate.session_id)
            if id(entry) in description_edits and self._save(entry):
                description_edits.discard(id(entry))
                baselines.pop(id(entry), None)

    def _new_session(self) -> None:
        harnesses = self._new_session_harnesses()
        if not harnesses:
            self._set_message("no installed agent harnesses found", True)
            return
        runner = self._choose_harness(harnesses)
        if runner is None:
            self._set_message("new session cancelled")
            return
        entry = SessionEntry(
            raw=f"{runner} New {runner} session",
            command=runner,
            separator=" ",
            description=f"New {runner} session",
            line_number=0,
            source="temporary",
        )
        self._remember_new_session(entry)
        self.document.add_temporary(entry)
        self.rebuild(entry)
        self._set_message(f"starting new {runner} session")
        self._launch_selected()

    def _draw_config(self, height: int, width: int) -> None:
        assert self.config_editor is not None
        _safe_addstr(self.stdscr, 0, 0, "Configuration editor", max(1, width - 1), curses.A_BOLD)
        config_count = len(self.config_editor.lines)
        config_visible = min(config_count, max(0, height - 3))
        for offset, item in enumerate(self.config_editor.lines[:config_visible]):
            selected = offset == self.config_selected
            prefix = "" if item.active else "# "
            text = f"{prefix}{item.key}={item.value}"
            _safe_addstr(
                self.stdscr,
                1 + offset,
                0,
                text,
                max(1, width - 1),
                self._attr("description", selected),
            )

        def example(
            command: str,
            description: str,
            *,
            active: bool = False,
            source: str = "file",
        ) -> SessionEntry:
            return SessionEntry(
                raw=f"{command} {description}",
                command=command,
                separator=" ",
                description=description,
                line_number=0,
                active=active,
                last_used_ns=1_700_000_000_000_000_000,
                source=source,
            )

        examples = [
            (example("omp --resume throwaway", "Temporary task Throwaway."), False),
            (example("omp --resume active", "Active session", active=True), False),
            (example("omp --resume selected", "Selected session"), True),
            (
                example("omp --resume new", "New unsaved session", source="temporary"),
                False,
            ),
            (example("omp --resume done", "Completed task Done."), False),
            (example("omp --resume normal", "Normal session"), False),
        ]
        example_header_y = 2 + config_count
        example_start_y = example_header_y + 1
        available_example_rows = max(0, height - 2 - example_start_y)
        example_count = 0
        if config_visible == config_count:
            example_count = min(available_example_rows, len(examples))
        if example_count:
            _safe_addstr(
                self.stdscr,
                example_header_y,
                0,
                "Examples (live colors)",
                max(1, width - 1),
                curses.A_BOLD,
            )
            example_rows = examples[:example_count]
            command_texts = [
                entry.short_command if self.config.short_commands else entry.command
                for entry, _ in example_rows
            ] if not self.config.hide_commands else [""]
            date_texts = [
                entry.last_used_text(self.config.get("date_format"))
                for entry, _ in example_rows
            ] if self.config.show_last_used_date else []
            date_width = max([len(text) for text in date_texts] + [0])
            date_gap = 1 if date_width else 0
            command_width = min(
                max(map(len, command_texts)),
                max(1, width - 7 - date_width - date_gap),
            )
            date_x = 5 + command_width
            description_x = date_x + date_width + date_gap
            for offset, (entry, selected) in enumerate(example_rows):
                y = example_start_y + offset
                _safe_addstr(
                    self.stdscr,
                    y,
                    0,
                    _status_marker(entry),
                    4,
                    self._attr("active", selected),
                )
                self._draw_command(y, 4, entry, command_width, selected)
                if date_width:
                    _safe_addstr(
                        self.stdscr,
                        y,
                        date_x,
                        entry.last_used_text(self.config.get("date_format")),
                        date_width,
                        self._attr("description", selected),
                    )
                desc_role = "throwaway" if entry.throwaway else ("done" if entry.done else "description")
                _safe_addstr(
                    self.stdscr,
                    y,
                    description_x,
                    entry.description,
                    max(0, width - description_x - 1),
                    self._attr(desc_role, selected),
                )

        footer_attr = self._attr("error" if self.message_is_error else "prompt")
        footer = self.message or "config mode"
        shortcuts = "CONFIG: ↑/↓ move  Space toggle/pick  ;/# comment  Enter pick/edit  q exit"
        _safe_addstr(self.stdscr, max(0, height - 2), 0, footer, max(1, width - 1), footer_attr)
        _safe_addstr(self.stdscr, max(0, height - 1), 0, shortcuts, max(1, width - 1), curses.A_DIM)

    def _edit_config_value(self) -> None:
        assert self.config_editor is not None
        if self._config_options(self.config_selected) is not None:
            self._pick_config_value()
            return
        item = self.config_editor.lines[self.config_selected]
        result = _read_line(self.stdscr, f"{item.key}= ", item.value)
        if result is None:
            self._set_message("config edit cancelled")
            return
        self.config_editor.set_value(self.config_selected, result)
        if self._save_config_editor():
            self._set_message(f"{item.key} updated")

    def _handle_config_key(self, key: int) -> None:
        assert self.config_editor is not None
        count = len(self.config_editor.lines)
        if key in (ord("q"), ord("Q"), 27):
            self.config_editor = None
            self._set_message("config mode off")
        elif key == curses.KEY_UP:
            self.config_selected = max(0, self.config_selected - 1)
        elif key == curses.KEY_DOWN:
            self.config_selected = min(max(0, count - 1), self.config_selected + 1)
        elif key == curses.KEY_PPAGE:
            page = max(1, self.stdscr.getmaxyx()[0] - 3)
            self.config_selected = max(0, self.config_selected - page)
        elif key == curses.KEY_NPAGE:
            page = max(1, self.stdscr.getmaxyx()[0] - 3)
            self.config_selected = min(max(0, count - 1), self.config_selected + page)
        elif key == curses.KEY_HOME:
            self.config_selected = 0
        elif key == curses.KEY_END:
            self.config_selected = max(0, count - 1)
        elif key == ord(" "):
            if self.config_editor.toggle_value(self.config_selected):
                if self._save_config_editor():
                    self._set_message("config value toggled")
            else:
                self._pick_config_value()
        elif key in (ord(";"), ord("#")):
            self.config_editor.toggle_comment(self.config_selected)
            if self._save_config_editor():
                self._set_message("config comment state toggled")
        elif key in (10, 13, curses.KEY_ENTER):
            self._edit_config_value()

    def _draw_help(self, height: int, width: int) -> None:
        lines = [
            "Aisema help",
            "",
            "Navigation",
            "  Up/Down       move one row",
            "  Page Up/Down  move one screen",
            "  Home/End      jump to first/last row",
            "",
            "Shortcuts (case-insensitive)",
            "  F             find",
            "  E             edit selected description",
            "  D             toggle done marker",
            "  S             choose sort mode",
            "  C             open config",
            "  X             toggle throwaway marker",
            "  N             start a new session",
            "  H             show help",
            "  Q             quit",
            "",
            "Commands",
            "  :find TEXT    search descriptions, IDs, and commands",
            "  :edit         edit the selected description",
            "  :done         toggle the done marker",
            "  :undone       remove the done marker",
            "  :sort MODE    sort the session list",
            "  :date [on|off] toggle last-used dates",
            "  :new session  choose a harness and start a temporary session",
            "  :config       edit config values",
            "  :help         show this help",
            "  Enter         launch or focus the selected session",
        ]
        for offset, line in enumerate(lines):
            if offset >= max(0, height - 1):
                break
            _safe_addstr(self.stdscr, offset, 0, line, max(1, width - 1))
        _safe_addstr(self.stdscr, max(0, height - 1), 0, "q/Esc close help", max(1, width - 1), curses.A_DIM)

    def _draw_command(
        self,
        y: int,
        x: int,
        entry: SessionEntry,
        width: int,
        selected: bool,
    ) -> None:
        text = entry.short_command if self.config.short_commands else entry.command
        if width <= 0:
            return
        runner_end = len(text.split(" ", 1)[0])
        session_id = entry.short_session_id if self.config.short_commands else entry.session_id
        hash_start = text.find(session_id) if session_id else -1
        if hash_start < runner_end:
            hash_start = -1
        cursor = x
        remaining = width

        def draw(segment: str, role: str) -> None:
            nonlocal cursor, remaining
            if not segment or remaining <= 0:
                return
            visible = min(len(segment), remaining)
            _safe_addstr(self.stdscr, y, cursor, segment, visible, self._attr(role, selected))
            cursor += visible
            remaining -= visible

        if hash_start < 0:
            draw(text[:runner_end], "runner")
            draw(text[runner_end:], "command")
            return
        draw(text[:runner_end], "runner")
        draw(text[runner_end:hash_start], "command")
        draw(text[hash_start : hash_start + len(session_id)], "hash")
        draw(text[hash_start + len(session_id) :], "command")

    def _draw(self) -> None:
        now = time.monotonic()
        if now - self.last_active_refresh >= 1.0:
            process.refresh_active(self.document.entries)
            self._refresh_new_sessions()
            self.last_active_refresh = now
            self.rebuild(self.visible[self.selected] if self.visible and self.selected < len(self.visible) else None)
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if self.config_editor is not None:
            self._draw_config(height, width)
            self.stdscr.refresh()
            return
        if self.help_visible:
            self._draw_help(height, width)
            self.stdscr.refresh()
            return
        title = f" sessions: {len(self.visible)}/{len(self.document.entries)} "
        if self.query:
            title += f"find={self.query!r} "
        title += f"sort={self.first_sort_mode}/{self.sort_mode}{' desc' if self.sort_descending else ''} "
        _safe_addstr(self.stdscr, 0, 0, title, max(1, width - 1), curses.A_BOLD)

        list_height = max(1, height - 3)
        command_texts = [
            entry.short_command if self.config.short_commands else entry.command
            for entry in self.visible
        ] if not self.config.hide_commands else [""]
        date_texts = [
            entry.last_used_text(self.config.get("date_format"))
            for entry in self.visible
        ] if self.show_last_used_date else []
        date_width = max([len(text) for text in date_texts] + [0])
        command_width = max([len(text) for text in command_texts] + [0])
        date_gap = 1 if date_width else 0
        command_width = min(command_width, max(1, width - 7 - date_width - date_gap))
        date_x = 5 + command_width
        description_x = date_x + date_width + date_gap
        for offset, entry in enumerate(self.visible[self.scroll_top : self.scroll_top + list_height]):
            index = self.scroll_top + offset
            selected = index == self.selected
            y = 1 + offset
            marker = _status_marker(entry)
            marker_attr = self._attr("active", selected)
            _safe_addstr(self.stdscr, y, 0, marker, 4, marker_attr)
            self._draw_command(y, 4, entry, command_width, selected)
            if date_width:
                _safe_addstr(
                    self.stdscr,
                    y,
                    date_x,
                    entry.last_used_text(self.config.get("date_format")),
                    date_width,
                    self._attr("description", selected),
                )
            remaining = max(0, width - description_x - 1)
            desc_role = "throwaway" if entry.throwaway else ("done" if entry.done else "description")
            desc_attr = self._attr(desc_role, selected)
            _safe_addstr(self.stdscr, y, description_x, entry.description, remaining, desc_attr)

        footer_attr = self._attr("error" if self.message_is_error else "prompt")
        footer = self.message or "ready"
        shortcuts = "F:find  E:edit  D:done  S:sort  C:config  X:throwaway  H:help  N:new session  Q:quit"
        _safe_addstr(self.stdscr, max(0, height - 2), 0, footer, max(1, width - 1), footer_attr)
        _safe_addstr(self.stdscr, max(0, height - 1), 0, shortcuts, max(1, width - 1), curses.A_DIM)
        self.stdscr.refresh()

    def _selected_entry(self) -> Optional[SessionEntry]:
        if not self.visible:
            return None
        return self.visible[self.selected]

    def _edit(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._set_message("no session selected", True)
            return
        if not entry.parsed:
            self._set_message("cannot edit an unrecognized session line", True)
            return

        if entry.temporary:
            self._refresh_new_sessions()
        result = _read_line(self.stdscr, "edit> ", entry.description)
        if result is None:
            self._set_message("edit cancelled")
            return
        entry.set_description(result)
        if entry.temporary:
            description_edits = getattr(self, "new_session_description_edits", set())
            description_edits.add(id(entry))
            for _ in range(10):
                if entry.session_id:
                    break
                time.sleep(0.1)
                self._refresh_new_sessions()
            if entry.temporary and not entry.session_id:
                self._set_message("session ID not discovered; description not saved", True)
                return
        if entry.source == "file":
            self._set_message("description saved")
        elif self._save(entry):
            getattr(self, "new_session_baselines", {}).pop(id(entry), None)
            getattr(self, "new_session_description_edits", set()).discard(id(entry))
            self._set_message("description saved")

    def _run_command(self, command: str) -> None:
        parts = command.strip().split(maxsplit=1)
        name = parts[0] if parts else ""
        argument = parts[1] if len(parts) == 2 else ""
        if name == ":find":
            self.query = argument.strip()
            self.rebuild()
            self._set_message(f"search: {self.query}" if self.query else "search cleared")
        elif name == ":edit":
            self._edit()
        elif name == ":done":
            entry = self._selected_entry()
            if entry is None:
                self._set_message("no session selected", True)
                return
            if not entry.parsed:
                self._set_message("cannot change an unrecognized session line", True)
                return
            entry.toggle_done()
            if self._save(entry, materialize=not entry.temporary):
                self._set_message("done marker toggled")
        elif name == ":undone":
            entry = self._selected_entry()
            if entry is None:
                self._set_message("no session selected", True)
                return
            if not entry.parsed:
                self._set_message("cannot change an unrecognized session line", True)
                return
            entry.mark_undone()
            if self._save(entry, materialize=not entry.temporary):
                self._set_message("done marker removed")
        elif name == ":sort":
            args = argument.split()
            mode = args[0].lower() if args else "date"
            descending = len(args) > 1 and args[1].lower() in {"desc", "descending"}
            try:
                _sort_entries(
                    self.document.entries,
                    mode,
                    descending,
                    getattr(self, "first_sort_mode", "status"),
                )
            except ValueError as exc:
                self._set_message(str(exc), True)
                return
            current = self._selected_entry()
            self.sort_mode = "date" if mode == "status" else mode
            self.sort_descending = descending
            self.rebuild(current)
            self._set_message(f"sorted by {mode}")
        elif name == ":new":
            self._new_session()
        elif name == ":date":
            value = argument.strip().lower()
            if value in {"on", "true", "yes"}:
                enabled = True
            elif value in {"off", "false", "no"}:
                enabled = False
            elif not value:
                enabled = not self.show_last_used_date
            else:
                self._set_message("usage: :date [on|off]", True)
                return
            current = self._selected_entry()
            self.show_last_used_date = enabled
            if enabled:
                refresh_last_used(self.document.entries)
            self.rebuild(current)
            self._set_message(f"last used date {'on' if enabled else 'off'}")
        elif name == ":config":
            try:
                self.config_editor = ConfigEditor(self.config.path)
            except OSError as exc:
                self._set_message(f"config open failed: {exc}", True)
            else:
                self.config_selected = 0
                self._set_message("config mode: q exits")
        elif name == ":help":
            self.help_visible = True
            self._set_message("")
        elif command.strip():
            self._set_message(f"unknown command: {command}", True)

    def _toggle_throwaway(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._set_message("no session selected", True)
            return
        if not entry.parsed:
            self._set_message("cannot change an unrecognized session line", True)
            return
        entry.toggle_throwaway()
        if entry.throwaway:
            self.throwaway_visible.add(id(entry))
        else:
            self.throwaway_visible.discard(id(entry))
        if self._save(entry, rebuild=False, materialize=not entry.temporary):
            self._set_message("throwaway marker toggled")

    def _launch_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._set_message("no session selected", True)
            return
        if not entry.parsed:
            self._set_message("cannot launch an unrecognized session line", True)
            return
        tracked_pane = getattr(self, "new_session_panes", {}).get(id(entry))
        if tracked_pane is not None:
            self._refresh_new_sessions()
            if tmux.focus(tracked_pane):
                entry.active = True
                self._set_message("focused running session")
            else:
                self._set_message("session is in tmux but focus failed", True)
            return
        match = process.find_process(entry)
        if match is not None:
            pane = tmux.pane_for_process(match)
            if pane is not None and tmux.focus(pane):
                self._set_message("focused running session")
            elif pane is not None:
                self._set_message("session is in tmux but focus failed", True)
            else:
                self._set_message("session is already active outside tmux", True)
            entry.active = True
            return

        if tmux.inside():
            pane = tmux.launch(entry.command, entry.description)
            if pane is not None:
                if entry.temporary:
                    getattr(self, "new_session_panes", {})[id(entry)] = pane
                entry.active = True
                self._refresh_new_sessions()
            else:
                self._set_message("tmux launch failed", True)
            return

        self._set_message("running session; press q in it to return")
        self._draw()
        try:
            curses.def_prog_mode()
            curses.endwin()
            subprocess.call(entry.command, shell=True, executable="/bin/sh")
        finally:
            curses.reset_prog_mode()
            self.stdscr.clear()
            self.stdscr.refresh()
        self._refresh_new_sessions()
        self._set_message("session exited")

    def _prompt_command(self, initial: str) -> None:
        command = _read_line(self.stdscr, "command> ", initial)
        if command is not None:
            self._run_command(command)

    def run(self) -> None:
        self.stdscr.keypad(True)
        self.stdscr.timeout(250)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        while True:
            self._draw()
            key = self.stdscr.getch()
            if key == -1:
                continue
            if self.config_editor is not None:
                self._handle_config_key(key)
                continue
            if self.help_visible:
                if key in (ord("q"), ord("Q"), 27):
                    self.help_visible = False
                    self._set_message("help closed")
                continue

            if key == curses.KEY_UP:
                self._move_selection(self.selected - 1)
            elif key == curses.KEY_DOWN:
                self._move_selection(self.selected + 1)
            elif key == curses.KEY_PPAGE:
                self._move_selection(self.selected - max(1, self.stdscr.getmaxyx()[0] - 3))
            elif key == curses.KEY_NPAGE:
                self._move_selection(self.selected + max(1, self.stdscr.getmaxyx()[0] - 3))
            elif key == curses.KEY_HOME:
                self._move_selection(0)
            elif key == curses.KEY_END:
                self._move_selection(len(self.visible) - 1)
            elif key in (10, 13, curses.KEY_ENTER):
                self._launch_selected()
            elif key == ord(":"):
                command = _read_line(self.stdscr, "command> ", ":")
                if command is not None:
                    self._run_command(command)
            elif key == 23:
                command = _read_line(self.stdscr, "command> ", ":find")
                if command is not None:
                    self._run_command(command)
            elif key in (ord("f"), ord("F")):
                self._prompt_command(":find")
            elif key in (ord("e"), ord("E")):
                self._edit()
            elif key in (ord("d"), ord("D")):
                self._run_command(":done")
            elif key in (ord("s"), ord("S")):
                selection = self._choose_sort_mode()
                if selection is not None:
                    layer, mode = selection
                    if layer == "first":
                        self.first_sort_mode = mode
                    else:
                        self.sort_mode = mode
                    self.rebuild(self._selected_entry())
                    self._set_message(f"sorted {layer} layer by {mode}")
            elif key in (ord("c"), ord("C")):
                self._run_command(":config")
            elif key in (ord("x"), ord("X")):
                self._toggle_throwaway()
            elif key in (ord("h"), ord("H")):
                self._run_command(":help")
            elif key in (ord("n"), ord("N")):
                self._new_session()
            elif key == 27:
                self.query = ""
                self.rebuild()
                self._set_message("search cleared")
            elif key in (ord("q"), ord("Q")):
                return


def run_tui(document: SessionDocument, config: Config) -> None:
    curses.wrapper(lambda stdscr: SessionApp(stdscr, document, config).run())
