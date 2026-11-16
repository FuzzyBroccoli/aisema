import curses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from session_manager.config import Config, DEFAULTS, load_config
from session_manager.config_editor import ConfigEditor
from session_manager.discovery import _generic_json_session, _resume_command, discover_recent
from session_manager.model import parse_line
from session_manager.process import ProcessMatch, find_process
from session_manager.storage import load_document
from session_manager import tmux
from session_manager.tui import (
    SessionApp,
    _color_value,
    _inspection_query_index,
    _sort_entries,
    _status_marker,
    _word_left,
    _word_right,
    run_tui,
)



class SessionManagerTests(unittest.TestCase):
    def test_tui_startup_reaches_main_screen(self) -> None:
        class Screen:
            def __init__(self) -> None:
                self.drawn_text: list[str] = []

            def keypad(self, enabled: bool) -> None:
                pass

            def timeout(self, milliseconds: int) -> None:
                pass

            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

            def erase(self) -> None:
                pass

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                self.drawn_text.append(text[:width])

            def refresh(self) -> None:
                pass

            def getch(self) -> int:
                return ord("q")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text("omp --resume startup-id Startup session\n", encoding="utf-8")
            screen = Screen()
            document = load_document(path)
            config = Config(DEFAULTS, path)
            with patch("session_manager.tui.curses.wrapper", lambda callback: callback(screen)):
                with patch("session_manager.tui.process.refresh_active"):
                    run_tui(document, config)
        self.assertTrue(any(text.startswith(" sessions: ") for text in screen.drawn_text))
    def test_m_opens_query_popup_and_closes_with_q(self) -> None:
        class Screen:
            def __init__(self) -> None:
                self.keys = iter([ord("m"), ord("q")])

            def keypad(self, enabled: bool) -> None:
                pass

            def timeout(self, milliseconds: int) -> None:
                pass

            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

            def erase(self) -> None:
                pass

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                pass

            def refresh(self) -> None:
                pass

            def getch(self) -> int:
                return next(self.keys)

        class Popup(Screen):
            def __init__(self) -> None:
                super().__init__()
                self.keys = iter([curses.KEY_UP, ord("q")])
                self.drawn: list[str] = []

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                self.drawn.append(text[:width])

            def box(self) -> None:
                pass

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            document = load_document(path)
            entry = parse_line("omp --resume inspect Inspect this session", 1)
            assert entry is not None
            entry.user_queries = ["First question", "Latest question"]
            document.lines.append(entry)
            screen = Screen()
            popup = Popup()
            with patch("session_manager.tui.curses.wrapper", lambda callback: callback(screen)):
                with patch("session_manager.tui.curses.newwin", return_value=popup):
                    with patch("session_manager.tui.process.refresh_active"):
                        run_tui(document, Config(DEFAULTS, path))
            self.assertIn("Inspect this session", popup.drawn)
            self.assertIn("Latest question", popup.drawn)
    def test_t_alias_toggles_throwaway_marker(self) -> None:
        class Screen:
            def __init__(self) -> None:
                self.keys = iter([ord("t"), ord("q")])

            def keypad(self, enabled: bool) -> None:
                pass

            def timeout(self, milliseconds: int) -> None:
                pass

            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

            def erase(self) -> None:
                pass

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                pass

            def refresh(self) -> None:
                pass

            def getch(self) -> int:
                return next(self.keys)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text("omp --resume throwaway Throwaway candidate\n", encoding="utf-8")
            document = load_document(path)
            entry = document.entries[0]
            screen = Screen()
            with patch("session_manager.tui.curses.wrapper", lambda callback: callback(screen)):
                with patch("session_manager.tui.process.refresh_active"):
                    run_tui(document, Config(DEFAULTS, path))
            self.assertTrue(entry.throwaway)


    def test_r_refresh_discovers_increasing_batches(self) -> None:
        class Screen:
            def __init__(self) -> None:
                self.keys = iter([ord("r"), ord("r"), ord("q")])

            def keypad(self, enabled: bool) -> None:
                pass

            def timeout(self, milliseconds: int) -> None:
                pass

            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

            def erase(self) -> None:
                pass

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                pass

            def refresh(self) -> None:
                pass

            def getch(self) -> int:
                return next(self.keys)

        discovered = [
            parse_line(f"omp --resume id-{index} Session {index}", index)
            for index in range(10)
        ]
        assert all(entry is not None for entry in discovered)
        entries = [entry for entry in discovered if entry is not None]
        calls: list[int] = []

        def discover(count, runners):
            calls.append(count)
            return entries[:count]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            document = load_document(path)
            screen = Screen()
            with patch("session_manager.tui.curses.wrapper", lambda callback: callback(screen)):
                with patch("session_manager.tui.discover_recent", side_effect=discover):
                    with patch("session_manager.tui.available_harnesses", return_value=("omp",)):
                        with patch("session_manager.tui.process.refresh_active"):
                            run_tui(document, Config(DEFAULTS, path))
            self.assertEqual(calls, [5, 10])
            self.assertEqual(len(document.ephemeral), 10)


    def test_parse_done_and_throwaway_markers(self) -> None:
        entry = parse_line("omp --resume abc-123 Work in progress", 1)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.command, "omp --resume abc-123")
        self.assertEqual(entry.description, "Work in progress")
        self.assertEqual(entry.session_id, "abc-123")
        self.assertEqual(entry.short_session_id, "123")
        self.assertEqual(entry.short_command, "omp 123")
        entry.last_used_ns = 1_700_000_000_000_000_000
        self.assertRegex(entry.last_used_text(), r"^\d{2}\.\d{2}\.\d{4}$")
        self.assertEqual(entry.last_used_text("%Y"), "2023")
        self.assertFalse(entry.done)
        self.assertFalse(entry.throwaway)

        entry.toggle_done()
        self.assertEqual(entry.description, "Work in progress Done.")
        self.assertTrue(entry.done)
        entry.toggle_done()
        self.assertEqual(entry.description, "Work in progress")

        throwaway = parse_line("omp --resume def456 Temporary Throwaway.", 2)
        assert throwaway is not None
        self.assertTrue(throwaway.throwaway)
        self.assertFalse(throwaway.done)
        throwaway.toggle_done()
        self.assertEqual(throwaway.description, "Temporary Done. Throwaway.")
        self.assertTrue(throwaway.done)
        self.assertTrue(throwaway.throwaway)
        throwaway.toggle_done()
        self.assertEqual(throwaway.description, "Temporary Throwaway.")

    def test_status_marker_keeps_fixed_status_columns(self) -> None:
        entry = parse_line("omp --resume marker Session Done. Throwaway.", 1)
        assert entry is not None
        entry.active = False
        self.assertEqual(_status_marker(entry), " DX ")
        entry.active = True
        self.assertEqual(_status_marker(entry), "ADX ")
        entry.source = "temporary"
        self.assertEqual(_status_marker(entry), "ADXN")
        entry.source = "discovered"
        self.assertEqual(_status_marker(entry), "ADXN")

    def test_new_session_adds_temporary_entry_in_list_order(self) -> None:
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text(
                "goose session resume --name old Goose session\n"
                "omp --resume newer OMP session\n",
                encoding="utf-8",
            )
            document = load_document(path)
            app = object.__new__(SessionApp)
            app.stdscr = Window()
            app.document = document
            app.config = Config(DEFAULTS, path)
            app.sort_mode = "status"
            app.sort_descending = False
            app.query = ""
            app.visible = document.entries.copy()
            app.selected = 0
            app.scroll_top = 0
            app.throwaway_visible = set()
            app.message = ""
            app.message_is_error = False

            with patch(
                "session_manager.tui.available_harnesses",
                return_value=("omp", "goose", "copilot"),
            ):
                self.assertEqual(app._new_session_harnesses(), ["goose", "omp", "copilot"])
                with patch.object(app, "_choose_harness", return_value="goose"):
                    with patch.object(app, "_launch_selected"):
                        app._new_session()

            entry = document.entries[-1]
            self.assertTrue(entry.temporary)
            self.assertEqual(entry.command, "goose")
            self.assertEqual(entry.description, "New goose session")
            self.assertEqual(path.read_text(encoding="utf-8"), "goose session resume --name old Goose session\nomp --resume newer OMP session\n")

    def test_new_session_prefers_agent_over_cursor_agent(self) -> None:
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text(
                "cursor-agent --resume stored Stored cursor session\n",
                encoding="utf-8",
            )
            document = load_document(path)
            app = object.__new__(SessionApp)
            app.stdscr = Window()
            app.document = document
            app.config = Config(DEFAULTS, path)
            app.sort_mode = "status"
            app.first_sort_mode = "date"
            app.sort_descending = False
            app.query = ""
            app.visible = document.entries.copy()
            app.selected = 0
            app.scroll_top = 0
            app.throwaway_visible = set()
            app.message = ""
            app.message_is_error = False
            app.new_session_baselines = {}
            app.new_session_description_edits = set()
            app.new_session_panes = {}

            with patch(
                "session_manager.tui.available_harnesses",
                return_value=("cursor-agent", "agent"),
            ):
                self.assertEqual(app._new_session_harnesses(), ["agent"])
                with patch.object(app, "_choose_harness", return_value="agent"):
                    with patch.object(app, "_launch_selected"):
                        app._new_session()

            self.assertEqual(document.entries[0].command, "cursor-agent --resume stored")
            self.assertEqual(document.entries[-1].command, "agent")


    def test_new_session_id_stays_temporary_until_description_edit(self) -> None:
        discovered = parse_line("omp --resume generated New session", 0)
        assert discovered is not None
        discovered.last_used_ns = 2
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            document = load_document(path)
            entry = parse_line("omp New omp session", 0)
            assert entry is not None
            entry.parsed = True
            entry.command = "omp"
            entry.separator = " "
            document.add_temporary(entry)
            app = object.__new__(SessionApp)
            app.stdscr = Window()
            app.document = document
            app.config = Config(DEFAULTS, path)
            app.sort_mode = "date"
            app.first_sort_mode = "status"
            app.sort_descending = False
            app.query = ""
            app.visible = [entry]
            app.selected = 0
            app.scroll_top = 0
            app.throwaway_visible = set()
            app.message = ""
            app.message_is_error = False
            app.new_session_baselines = {id(entry): {"existing"}}
            app.new_session_description_edits = set()
            with patch(
                "session_manager.tui.discover_recent",
                side_effect=[[], [], [discovered]],
            ):
                app._refresh_new_sessions()
                self.assertEqual(entry.command, "omp")
                self.assertEqual(entry.session_id, "")
                self.assertTrue(entry.temporary)
                self.assertEqual(path.read_text(encoding="utf-8"), "")

                with patch("session_manager.tui._read_line", return_value="Edited session"):
                    app._edit()
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "omp --resume generated Edited session",
            )
            self.assertFalse(entry.temporary)

    def test_enter_focuses_tmux_pane_created_for_new_session(self) -> None:
        entry = parse_line("omp New omp session", 0)
        assert entry is not None
        entry.parsed = True
        entry.command = "omp"
        entry.separator = " "
        entry.source = "temporary"
        discovered = parse_line("omp --resume generated New session", 0)
        assert discovered is not None
        class Document:
            entries = [entry]

        pane = tmux.Pane("%3", 123, "@4", "main")
        app = object.__new__(SessionApp)
        app.document = Document()
        app.visible = [entry]
        app.selected = 0
        app.new_session_panes = {id(entry): pane}
        app.new_session_baselines = {id(entry): {"existing"}}
        app.new_session_description_edits = set()
        app.message = ""
        app.message_is_error = False
        with patch("session_manager.tui.discover_recent", return_value=[discovered]):
            with patch("session_manager.tui.tmux.focus", return_value=True) as focus:
                with patch("session_manager.tui.process.find_process") as find_process:
                    app._launch_selected()
        focus.assert_called_once_with(pane)
        find_process.assert_not_called()
        self.assertEqual(entry.session_id, "generated")
        self.assertTrue(entry.active)

    def test_word_navigation_skips_whitespace_and_words(self) -> None:
        buffer = list("one two three")
        self.assertEqual(_word_left(buffer, len(buffer)), 8)
        self.assertEqual(_word_left(buffer, 8), 4)
        self.assertEqual(_word_right(buffer, 0), 3)
        self.assertEqual(_word_right(buffer, 4), 7)

    def test_session_list_page_and_home_end_navigation(self) -> None:
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (8, 80)

        app = object.__new__(SessionApp)
        app.stdscr = Window()
        app.visible = list(range(12))
        app.selected = 5
        app.scroll_top = 1

        app._move_selection(app.selected - 5)
        self.assertEqual((app.selected, app.scroll_top), (0, 0))
        app._move_selection(app.selected + 5)
        self.assertEqual((app.selected, app.scroll_top), (5, 1))
        app._move_selection(len(app.visible) - 1)
        self.assertEqual((app.selected, app.scroll_top), (11, 7))
        app._move_selection(0)
        self.assertEqual((app.selected, app.scroll_top), (0, 0))


    def test_named_colors_use_terminal_aliases(self) -> None:
        self.assertEqual(_color_value("pink")[0], curses.COLOR_MAGENTA)
        self.assertEqual(_color_value("light_gray")[0], curses.COLOR_BLACK)
        self.assertEqual(_color_value("dark_gray")[0], curses.COLOR_BLACK)
    def test_color_setup_exercises_every_tui_color_role(self) -> None:
        app = object.__new__(SessionApp)
        app.config = Config(DEFAULTS, Path("config"))
        app.color_pairs = {}
        roles = (
            "active",
            "runner",
            "hash",
            "command",
            "description",
            "done",
            "throwaway",
            "selected",
            "prompt",
            "error",
        )
        with patch("session_manager.tui.curses.start_color"):
            with patch("session_manager.tui.curses.use_default_colors"):
                with patch("session_manager.tui.curses.init_pair"):
                    with patch(
                        "session_manager.tui.curses.color_pair",
                        side_effect=lambda number: number,
                    ):
                        app._setup_colors()
        self.assertEqual(set(app.color_pairs), set(roles))


    def test_tmux_window_name_uses_description_prefix(self) -> None:
        self.assertEqual(tmux._window_name("Short description"), "Short")
        self.assertEqual(tmux._window_name("12345678901234567890"), "1234567890123456")
        self.assertEqual(tmux._window_name(""), "session")
        with patch("session_manager.tmux.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "%3\t123\t@4\tmain\n"
            pane = tmux.launch("omp --resume abc", "A description")
            self.assertEqual(pane, tmux.Pane("%3", 123, "@4", "main"))
            self.assertEqual(
                run.call_args.args[0],
                [
                    "tmux",
                    "new-window",
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}\t#{pane_pid}\t#{window_id}\t#{session_id}",
                    "-n",
                    "A",
                    "omp --resume abc",
                ],
            )

    def test_terminal_session_metadata_marks_process_active(self) -> None:
        session_id = "abc-123"
        entry = parse_line(f"omp --resume {session_id} Active", 1)
        assert entry is not None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markers = root / "terminal-sessions"
            markers.mkdir()
            (markers / "pts-7").write_text(
                f"/tmp\n/tmp/session_{session_id}.jsonl\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(root)}):
                with patch(
                    "session_manager.process.iter_processes",
                    return_value=[ProcessMatch(42, ("omp",))],
                ):
                    with patch("session_manager.process._process_tty", return_value="/dev/pts/7"):
                        self.assertEqual(find_process(entry), ProcessMatch(42, ("omp",)))

    def test_storage_preserves_blank_lines_and_separator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text(
                "omp --resume abc  First\n\nomp --resume def Second\n",
                encoding="utf-8",
            )
            document = load_document(path)
            document.entries[0].set_description("Changed")
            document.save()
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "omp --resume abc  Changed\n\nomp --resume def Second\n",
            )

    def test_discovered_description_is_materialized_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text("omp --resume abc Existing\n", encoding="utf-8")
            document = load_document(path)
            discovered = parse_line("omp --resume def Discovered", 0)
            assert discovered is not None
            document.add_discovered([discovered])
            discovered.set_description("Edited discovered")
            document.persist_entry(discovered)
            document.save()
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "omp --resume abc Existing\nomp --resume def Edited discovered\n",
            )

    def test_temporary_description_is_materialized_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            document = load_document(path)
            temporary = parse_line("omp --resume new New session", 0)
            assert temporary is not None
            document.add_temporary(temporary)
            temporary.set_description("Edited new session")
            document.persist_entry(temporary)
            document.save()
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "omp --resume new Edited new session",
            )

    def test_done_entries_sort_before_throwaway_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text(
                "omp --resume z Zebra\n"
                "omp --resume a Alpha Done.\n"
                "omp --resume b Beta\n"
                "omp --resume t Temporary Throwaway.\n",
                encoding="utf-8",
            )
            entries = load_document(path).entries
            self.assertEqual(
                [entry.session_id for entry in _sort_entries(entries, "status", False)],
                ["z", "b", "a", "t"],
            )
            self.assertEqual(
                [entry.session_id for entry in _sort_entries(entries, "id", False)],
                ["b", "z", "a", "t"],
            )

    def test_status_sort_uses_newest_last_used_date_within_groups(self) -> None:
        entries = []
        for line_number, line in enumerate(
            (
                "omp --resume old Current",
                "omp --resume done-old Archived Done.",
                "omp --resume newest Current",
                "omp --resume done-new Archived Done.",
                "omp --resume throwaway Temporary Throwaway.",
            ),
            start=1,
        ):
            entry = parse_line(line, line_number)
            assert entry is not None
            entries.append(entry)
        entries[0].last_used_ns = 1
        entries[1].last_used_ns = 2
        entries[2].last_used_ns = 4
        entries[3].last_used_ns = 3
        entries[0].active = True

        self.assertEqual(
            [entry.session_id for entry in _sort_entries(entries, "status", False)],
            ["old", "newest", "done-new", "done-old", "throwaway"],
        )

    def test_sort_first_layer_groups_by_command_and_none(self) -> None:
        entries = []
        for line_number, line in enumerate(
            (
                "omp --resume z OMP later",
                "goose session resume --name g Goose first",
                "omp --resume a OMP first",
            ),
            start=1,
        ):
            entry = parse_line(line, line_number)
            assert entry is not None
            entries.append(entry)

        self.assertEqual(
            [entry.session_id for entry in _sort_entries(entries, "id", False, "command")],
            ["g", "a", "z"],
        )
        self.assertEqual(
            [entry.session_id for entry in _sort_entries(entries, "id", False, "none")],
            ["a", "g", "z"],
        )

    def test_throwaway_toggle_stays_visible_until_reload(self) -> None:
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text("omp --resume throwaway Temporary\n", encoding="utf-8")
            document = load_document(path)
            config = Config({**DEFAULTS, "show_throwaway": "false"}, path)
            app = object.__new__(SessionApp)
            app.stdscr = Window()
            app.document = document
            app.config = config
            app.sort_mode = "status"
            app.sort_descending = False
            app.query = ""
            app.visible = [document.entries[0]]
            app.selected = 0
            app.scroll_top = 0
            app.throwaway_visible = set()
            app.message = ""
            app.message_is_error = False

            app._toggle_throwaway()
            entry = document.entries[0]
            self.assertTrue(entry.throwaway)
            self.assertEqual(app.visible, [entry])
            self.assertIn(id(entry), app.throwaway_visible)
            app.rebuild(entry)
            self.assertEqual(app.visible, [entry])

            app._toggle_throwaway()
            self.assertFalse(entry.throwaway)
            self.assertNotIn(id(entry), app.throwaway_visible)
            self.assertEqual(app.visible, [entry])

    def test_config_creates_and_appends_commented_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            config = load_config(path)
            self.assertEqual(config.values, DEFAULTS)
            self.assertFalse(config.show_throwaway)
            self.assertTrue(config.show_done)
            self.assertFalse(config.show_last_used_date)
            self.assertEqual(config.get("date_format"), "%d.%m.%Y")
            self.assertEqual(
                config.sessions_file,
                Path.home() / ".config" / "aisema" / "sessions.txt",
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("# sessions_file=", text)
            self.assertIn("# show_throwaway=false", text)
            self.assertIn("# show_done=true", text)
            self.assertIn("# show_last_used_date=false", text)
            path.write_text(
                "display_command=short\nshow_throwaway=true\nshow_done=false\nshow_last_used_date=true\n"
                "date_format=%Y-%m-%d\n",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertTrue(config.short_commands)
            self.assertTrue(config.show_throwaway)
            self.assertFalse(config.show_done)
            self.assertTrue(config.show_last_used_date)
            self.assertEqual(config.get("date_format"), "%Y-%m-%d")
            path.write_text("display_command=none\n", encoding="utf-8")
            config = load_config(path)
            self.assertTrue(config.hide_commands)
            self.assertIn("# color_command=blue", path.read_text(encoding="utf-8"))
    def test_show_done_false_filters_done_sessions(self) -> None:
        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (24, 80)

        done = parse_line("omp --resume done Finished Done.", 1)
        current = parse_line("omp --resume current Current", 2)
        assert done is not None
        assert current is not None

        class Document:
            entries = [done, current]

        app = object.__new__(SessionApp)
        app.stdscr = Window()
        app.document = Document()
        app.config = Config({**DEFAULTS, "show_done": "false"}, Path("config"))
        app.first_sort_mode = "status"
        app.sort_mode = "date"
        app.sort_descending = False
        app.query = ""
        app.visible = []
        app.selected = 0
        app.scroll_top = 0
        app.throwaway_visible = set()
        app.rebuild()
        self.assertEqual(app.visible, [current])


    def test_date_command_toggles_live_display(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.txt"
            path.write_text("omp --resume date Session\n", encoding="utf-8")
            app = object.__new__(SessionApp)
            app.document = load_document(path)
            app.visible = []
            app.show_last_used_date = False
            app.message = ""
            app.message_is_error = False
            app.rebuild = lambda selected=None: None
            with patch("session_manager.tui.refresh_last_used") as refresh:
                app._run_command(":date")
                self.assertTrue(app.show_last_used_date)
                refresh.assert_called_once_with(app.document.entries)
                app._run_command(":date off")
                self.assertFalse(app.show_last_used_date)

    def test_config_preview_includes_selected_line(self) -> None:
        markers: list[str] = []
        texts: list[str] = []

        class Window:
            def getmaxyx(self) -> tuple[int, int]:
                return (30, 100)

            def addnstr(self, y: int, x: int, text: str, width: int, attr: int = 0) -> None:
                if x == 0 and width == 4:
                    markers.append(text)
                texts.append(text)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            editor = ConfigEditor(path)
            app = object.__new__(SessionApp)
            app.stdscr = Window()
            app.config_editor = editor
            app.config_selected = 0
            app.config = Config(
                {
                    **DEFAULTS,
                    "display_command": "none",
                    "show_last_used_date": "true",
                    "date_format": "%Y",
                },
                path,
            )
            app.color_pairs = {}
            app.message = ""
            app.message_is_error = False
            command_widths: list[int] = []
            drawn: list[tuple[str, bool]] = []
            def draw_command(y: int, x: int, entry, width: int, selected: bool) -> None:
                command_widths.append(width)
                drawn.append((entry.description, selected))

            app._draw_command = draw_command

            app._draw_config(30, 100)

            self.assertIn(("Selected session", True), drawn)
            self.assertIn(("Completed task Done.", False), drawn)
            self.assertIn("2023", texts)
            self.assertTrue(command_widths)
            self.assertTrue(all(width == 0 for width in command_widths))
            self.assertIn("A   ", markers)
            self.assertIn("   N", markers)


    def test_config_editor_changes_values_without_changing_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("# display_command=full\n# show_throwaway=false\n", encoding="utf-8")
            editor = ConfigEditor(path)
            show_index = next(index for index, item in enumerate(editor.lines) if item.key == "show_throwaway")
            editor.toggle_comment(show_index)
            self.assertTrue(editor.toggle_value(show_index))
            editor.save()
            text = path.read_text(encoding="utf-8")
            self.assertIn("show_throwaway=true", text)
            self.assertIn("# display_command=full", text)
            self.assertNotIn("show_throwaway=truex", text)
    def test_config_editor_removes_duplicate_known_keys_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text(
                "display_command=short\n"
                "# display_command=full\n"
                "# show_throwaway=false\n"
                "# show_throwaway=false\n"
                "show_last_used_date=true\n"
                "# show_last_used_date=false\n",
                encoding="utf-8",
            )
            editor = ConfigEditor(path)
            editor.save()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(line.lstrip("# ").startswith("display_command=") for line in lines), 1)
            self.assertEqual(sum(line.lstrip("# ").startswith("show_throwaway=") for line in lines), 1)
            self.assertEqual(sum(line.lstrip("# ").startswith("show_last_used_date=") for line in lines), 1)


    def test_config_loader_does_not_duplicate_commented_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("# display_command=full\n# show_throwaway=false\n", encoding="utf-8")
            load_config(path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("# display_command=full"), 1)
            self.assertEqual(text.count("# show_throwaway=false"), 1)

    def test_available_harnesses_uses_executable_lookup(self) -> None:
        with patch(
            "session_manager.discovery.shutil.which",
            side_effect=lambda name: f"/bin/{name}" if name in {"omp", "goose"} else None,
        ):
            from session_manager.discovery import available_harnesses

            self.assertEqual(available_harnesses(), ("omp", "goose"))

    def test_cursor_prompt_cleanup_is_runner_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cursor.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "role": "user",
                        "content": "<timestamp>2026-11-16T12:00:00Z</timestamp>"
                        "<user_query> Fix the cursor prompt </user_query>",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            cursor = _generic_json_session("cursor-agent", path, "cursor-id")
            other = _generic_json_session("agent", path, "agent-id")
        assert cursor is not None
        assert other is not None
        self.assertEqual(cursor.description, "Fix the cursor prompt")
        self.assertIn("<timestamp>", other.description)
        self.assertIn("<user_query>", other.description)
    def test_discovery_preserves_all_user_queries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(
                json.dumps(
                    {
                        "sessionId": "agent-id",
                        "messages": [
                            {"role": "user", "content": "First question"},
                            {"role": "assistant", "content": "Answer"},
                            {"role": "user", "content": "Latest question"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            session = _generic_json_session("agent", path)
        assert session is not None
        self.assertEqual(session.user_queries, ("First question", "Latest question"))

    def test_inspection_query_navigation(self) -> None:
        self.assertEqual(_inspection_query_index(None, 3, curses.KEY_UP, 2), 2)
        self.assertEqual(_inspection_query_index(2, 3, curses.KEY_UP, 2), 1)
        self.assertEqual(_inspection_query_index(1, 3, curses.KEY_DOWN, 2), 2)
        self.assertEqual(_inspection_query_index(2, 3, curses.KEY_PPAGE, 2), 0)
        self.assertEqual(_inspection_query_index(0, 3, curses.KEY_NPAGE, 2), 2)
        self.assertEqual(_inspection_query_index(2, 3, curses.KEY_HOME, 2), 0)
        self.assertEqual(_inspection_query_index(0, 3, curses.KEY_END, 2), 2)


    def test_discover_recent_merges_harnesses_by_update_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copilot = root / ".copilot" / "history-session-state" / "copilot-id.json"
            omp = root / ".omp" / "sessions" / "-project" / "2026-11-12T00:00:00Z_omp-id.jsonl"
            copilot.parent.mkdir(parents=True)
            omp.parent.mkdir(parents=True)
            copilot.write_text(
                json.dumps(
                    {
                        "sessionId": "copilot-id",
                        "messages": [{"role": "user", "content": "Copilot prompt"}],
                    }
                ),
                encoding="utf-8",
            )
            omp.write_text(
                json.dumps({"type": "session", "id": "omp-id"})
                + "\n"
                + json.dumps(
                    {
                        "type": "message",
                        "message": {"role": "user", "content": "OMP prompt"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(copilot, (1, 1))
            os.utime(omp, (2, 2))
            with patch("session_manager.discovery.Path.home", return_value=root):
                with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(root / ".omp")}):
                    entries = discover_recent("all", runners=("copilot", "omp"))
                    newest = discover_recent(1, runners=("copilot", "omp"))
            self.assertEqual([entry.session_id for entry in entries], ["omp-id", "copilot-id"])
            self.assertEqual(newest[0].command, "omp --resume omp-id")

    def test_resume_command_formats(self) -> None:
        self.assertEqual(_resume_command("copilot", "copilot-id"), "copilot --resume copilot-id")
        self.assertEqual(_resume_command("agent", "agent-id"), "agent --resume agent-id")
        self.assertEqual(
            _resume_command("cursor-agent", "cursor-id"),
            "cursor-agent --resume=cursor-id",
        )
        self.assertEqual(_resume_command("omp", "omp-id"), "omp --resume omp-id")
        self.assertEqual(
            _resume_command("opencode", "opencode-id"),
            "opencode --session opencode-id",
        )
        self.assertEqual(
            _resume_command("goose", "goose-id"),
            "goose session resume --name goose-id",
        )

    def test_parse_supported_resume_command_shapes(self) -> None:
        cursor = parse_line("cursor-agent --resume=cursor_1 Cursor", 1)
        assert cursor is not None
        self.assertEqual(cursor.session_id, "cursor_1")
        self.assertEqual(parse_line("opencode --session ses_1 OpenCode", 1).session_id, "ses_1")
        self.assertEqual(
            parse_line("goose session resume --name goose_1 Goose", 1).session_id,
            "goose_1",
        )
    def test_parse_resume_commands_with_option_flags(self) -> None:
        before = parse_line("omp --yolo --resume abc --verbose Before and after", 1)
        assert before is not None
        self.assertEqual(before.command, "omp --yolo --resume abc --verbose")
        self.assertEqual(before.description, "Before and after")

        after = parse_line("opencode --fast --session ses_1 --json OpenCode", 2)
        assert after is not None
        self.assertEqual(after.command, "opencode --fast --session ses_1 --json")
        self.assertEqual(after.description, "OpenCode")

    def test_cli_without_selector_uses_only_sessions_file(self) -> None:
        import session_manager.__main__ as main_module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_file = root / "sessions.txt"
            config_file = root / "config"
            session_file.write_text("omp --resume local-id Local session\n", encoding="utf-8")
            with patch.object(main_module, "available_harnesses", return_value=("omp",)):
                with patch.object(main_module, "discover_recent", return_value=[]):
                    with patch.object(main_module, "run_tui") as run_tui:
                        with patch.object(main_module.process, "refresh_active"):
                            with patch.object(
                                sys,
                                "argv",
                                [
                                    "aisema",
                                    "--file",
                                    str(session_file),
                                    "--config",
                                    str(config_file),
                                ],
                            ):
                                main_module.main()
            document = run_tui.call_args.args[0]
            self.assertEqual(len(document.ephemeral), 0)
            self.assertEqual(len(document.entries), 1)
    def test_cli_uses_configured_sessions_file_without_file_override(self) -> None:
        import session_manager.__main__ as main_module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_file = root / "configured-sessions.txt"
            config_file = root / "config"
            session_file.write_text("omp --resume configured Configured session\n", encoding="utf-8")
            config_file.write_text(f"sessions_file={session_file}\n", encoding="utf-8")
            with patch.object(main_module, "available_harnesses", return_value=("omp",)):
                with patch.object(main_module, "discover_recent", return_value=[]):
                    with patch.object(main_module.process, "refresh_active"):
                        with patch.object(main_module, "run_tui") as run_tui:
                            with patch.object(
                                sys,
                                "argv",
                                ["aisema", "--config", str(config_file)],
                            ):
                                main_module.main()
            document = run_tui.call_args.args[0]
            self.assertEqual([entry.session_id for entry in document.entries], ["configured"])


    def test_cli_without_selector_adds_active_sessions(self) -> None:
        import session_manager.__main__ as main_module

        active = parse_line("omp --resume active Active session", 0)
        assert active is not None

        def mark_active(entries) -> None:
            entries[0].active = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_file = root / "sessions.txt"
            config_file = root / "config"
            session_file.write_text("", encoding="utf-8")
            with patch.object(main_module, "available_harnesses", return_value=("omp",)):
                with patch.object(main_module, "discover_recent", return_value=[active]):
                    with patch.object(main_module.process, "refresh_active", side_effect=mark_active):
                        with patch.object(main_module, "run_tui") as run_tui:
                            with patch.object(
                                sys,
                                "argv",
                                [
                                    "aisema",
                                    "--file",
                                    str(session_file),
                                    "--config",
                                    str(config_file),
                                ],
                            ):
                                main_module.main()
            document = run_tui.call_args.args[0]
            self.assertEqual([entry.session_id for entry in document.ephemeral], ["active"])
            self.assertTrue(document.ephemeral[0].active)

    def test_discover_recent_omp_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "sessions" / "-project" / "2026-11-12T00:00:00Z_abc.jsonl"
            second = root / "sessions" / "-project" / "2026-11-12T00:01:00Z_def.jsonl"
            first.parent.mkdir(parents=True)
            first.write_text(
                json.dumps({"type": "title", "title": "Older"}) + "\n"
                + json.dumps({"type": "session", "id": "abc"}) + "\n",
                encoding="utf-8",
            )
            second.write_text(
                json.dumps({"type": "title", "title": "Newest"}) + "\n"
                + json.dumps({"type": "session", "id": "def"}) + "\n"
                + json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "Find this session"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            os.utime(first, (1, 1))
            os.utime(second, (2, 2))
            with patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(root)}):
                entries = discover_recent("omp", 1)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].session_id, "def")
            self.assertEqual(entries[0].description, "Find this session")
            self.assertEqual(entries[0].last_used_ns, 2_000_000_000)


if __name__ == "__main__":
    unittest.main()
