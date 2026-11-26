from __future__ import annotations

from contextlib import contextmanager
import curses
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runner_plugin_fixture import PLUGIN_SOURCE, create_fixture
from session_manager.config import Config, DEFAULTS
from session_manager.discovery import available_harnesses, discover_recent
from session_manager.model import parse_line
from session_manager import process
from session_manager.runner_registry import RunnerPluginError, load_registry
from session_manager.storage import load_document
from session_manager.tui import SessionApp, _status_marker


class Screen:
    """Only replace terminal rendering; discovery, storage and launches stay real."""

    def getmaxyx(self):
        return (24, 80)

    def clear(self):
        return None

    def refresh(self):
        return None


@contextmanager
def enter_on_stdin():
    """Give the real foreground subprocess one Enter without touching its launcher."""
    saved = os.dup(0)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"\n")
        os.close(write_fd)
        write_fd = -1
        os.dup2(read_fd, 0)
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


class RunnerPluginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = create_fixture(Path(temporary.name))
        self.environment = patch.dict(os.environ, self.fixture.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.registry = load_registry(self.fixture.plugin_dir)
        self.singleton = patch("session_manager.runner_registry._registry", self.registry)
        self.singleton.start()
        self.addCleanup(self.singleton.stop)

    def app(self, document=None):
        if document is None:
            document = load_document(self.fixture.sessions)
        with patch.object(SessionApp, "_setup_colors"):
            return SessionApp(Screen(), document, Config(DEFAULTS, self.fixture.config))

    def entry(self, line="harri-cli attach h-001   Investigate plugin boundary"):
        entry = parse_line(line, 1)
        self.assertIsNotNone(entry)
        return entry

    def test_drop_in_discovery_inspection_and_global_limit(self):
        entries = discover_recent("all")
        self.assertEqual([(item.runner, item.session_id, item.description) for item in entries],
                         [("harri", "h-001", "Investigate plugin boundary")])
        entry = entries[0]
        self.assertEqual(entry.user_queries, ["Check API"])
        self.assertEqual(self.app()._inspection_items_for_entry(entry),
                         [(1_000_000_000, "recap", "Prior recap"),
                          (2_000_000_000, "query", "Check API")])
        built_in = self.fixture.home / ".copilot" / "sessions" / "older.json"
        built_in.parent.mkdir(parents=True)
        built_in.write_text(json.dumps({"id": "older", "title": "Older built-in",
                                        "updatedAt": 2}), encoding="utf-8")
        (self.fixture.bin / "copilot").symlink_to(self.fixture.executable)
        self.assertEqual([(item.runner, item.session_id) for item in discover_recent(1)],
                         [("harri", "h-001")])
        self.assertEqual({item.runner for item in discover_recent("all")},
                         {"copilot", "harri"})

    def test_saved_identity_exact_spacing_and_edits(self):
        line = "harri-cli attach h-001   Investigate plugin boundary"
        self.fixture.sessions.write_text(line + "\n", encoding="utf-8")
        document = load_document(self.fixture.sessions)
        entry = document.entries[0]
        self.assertTrue(entry.parsed)
        self.assertEqual((entry.runner, entry.session_id, entry.short_command),
                         ("harri", "h-001", "harri 001"))
        self.assertEqual((entry.command, entry.separator, entry.serialize()),
                         ("harri-cli attach h-001", "   ", line))
        discovered = discover_recent("all")[0]
        self.assertEqual((discovered.runner, discovered.session_id),
                         (entry.runner, entry.session_id))
        entry.set_description("Edited session")
        entry.toggle_done()
        entry.toggle_throwaway()
        document.save()
        self.assertTrue(entry.done)
        self.assertTrue(entry.throwaway)
        saved = self.fixture.sessions.read_text(encoding="utf-8")
        self.assertEqual(saved, entry.serialize() + "\n")
        self.assertTrue(saved.startswith("harri-cli attach h-001   Edited session"))
        reloaded = load_document(self.fixture.sessions).entries[0]
        self.assertEqual((reloaded.command, reloaded.separator, reloaded.runner,
                          reloaded.session_id, reloaded.done, reloaded.throwaway),
                         (entry.command, "   ", "harri", "h-001", True, True))

    def test_unavailable_executable_does_not_disable_explicit_discovery_or_parsing(self):
        self.fixture.executable.unlink()
        entry = self.entry()
        self.assertTrue(entry.parsed)
        self.assertEqual((entry.runner, entry.session_id), ("harri", "h-001"))
        self.assertNotIn("harri", available_harnesses())
        self.assertEqual(discover_recent("all"), [])
        self.assertNotIn("harri", self.app()._new_session_harnesses())
        explicit = discover_recent("all", runners=("harri",))
        self.assertEqual(explicit[0].user_queries, ["Check API"])
        self.assertEqual(explicit[0].inspection_items[-1],
                         (2_000_000_000, "query", "Check API"))

    def test_missing_plugin_keeps_custom_saved_line_visible_and_launch_blocked(self):
        self.fixture.plugin.unlink()
        registry = load_registry(self.fixture.plugin_dir)
        with patch("session_manager.runner_registry._registry", registry):
            entry = self.entry()
            self.assertFalse(entry.parsed)
            self.assertEqual(entry.serialize(),
                             "harri-cli attach h-001   Investigate plugin boundary")
            self.assertEqual(entry.session_id, "")
            document = load_document(self.fixture.sessions)
            document.add_discovered([entry])
            app = self.app(document)
            with patch("session_manager.tui.subprocess.call") as launch:
                app._launch_selected()
            launch.assert_not_called()
            self.assertTrue(app.message_is_error)
            self.assertIn("unrecognized", app.message)

    def test_identity_deduplication_and_process_ownership(self):
        plugin_entry = discover_recent("all")[0]
        other = self.entry("copilot --resume h-001 Separate runner")
        document = load_document(self.fixture.sessions)
        document.add_discovered([plugin_entry, other, discover_recent("all")[0]])
        self.assertEqual({(entry.runner, entry.session_id) for entry in document.entries},
                         {("harri", "h-001"), ("copilot", "h-001")})
        self.assertEqual(len(document.entries), 2)
        running = process.ProcessMatch(51001, ("/fixture/harri-cli", "attach", "h-001"))
        snapshot = process.ProcessSnapshot(
            processes=(running,), by_runner={"harri-cli": (running,)},
            command_lines={running.pid: " ".join(running.argv)})
        process.refresh_active(document.entries, snapshot)
        self.assertEqual({entry.runner for entry in document.entries if entry.active}, {"harri"})

    def test_process_scan_still_excludes_other_users(self):
        current_uid = os.getuid()
        children = [SimpleNamespace(name="51001", stat=lambda: SimpleNamespace(st_uid=current_uid)),
                    SimpleNamespace(name="51002", stat=lambda: SimpleNamespace(st_uid=current_uid + 1)),
                    SimpleNamespace(name="self")]
        with patch.object(Path, "iterdir", return_value=iter(children)), patch(
            "session_manager.process._read_argv", return_value=("harri-cli", "attach", "h-001")
        ) as read_argv:
            found = tuple(process.iter_processes())
        self.assertEqual(found, (process.ProcessMatch(51001, ("harri-cli", "attach", "h-001")),))
        read_argv.assert_called_once_with(51001)

    def test_omp_marker_cache_is_shared_and_does_not_activate_other_runners(self):
        marker_root = self.fixture.home / ".omp" / "agent" / "terminal-sessions"
        marker_root.mkdir(parents=True)
        (marker_root / "pts-91").write_text("metadata\n/tmp/session_shared.jsonl\n", encoding="utf-8")
        entries = [self.entry("omp --resume shared First"),
                   self.entry("omp --resume shared Second"),
                   self.entry("harri-cli attach shared Plugin"),
                   self.entry("copilot --resume shared Other")]
        omp = process.ProcessMatch(52001, ("omp",))
        harri = process.ProcessMatch(52002, ("harri-cli",))
        copilot = process.ProcessMatch(52003, ("copilot",))
        original_iterdir = Path.iterdir
        marker_reads = []

        def iterdir(path):
            if path == marker_root:
                marker_reads.append(path)
            return original_iterdir(path)

        with patch("session_manager.process.iter_processes", return_value=iter((omp, harri, copilot))) as scan, patch.object(
            Path, "iterdir", iterdir
        ), patch("session_manager.process._process_tty", return_value="/dev/pts/91") as tty:
            snapshot = process.scan_processes()
            process.refresh_active(entries, snapshot)
            process.refresh_active(entries, snapshot)
        scan.assert_called_once()
        self.assertEqual(marker_reads, [marker_root])
        tty.assert_called_once_with(omp.pid)
        self.assertEqual([entry.active for entry in entries], [True, True, False, False])
        self.assertIn("omp", snapshot.runner_data)
        (marker_root / "pts-91").unlink()
        marker_reads.clear()
        with patch("session_manager.process.iter_processes", return_value=iter((omp,))), patch.object(
            Path, "iterdir", iterdir
        ):
            empty_snapshot = process.scan_processes()
            process.refresh_active(entries[:2], empty_snapshot)
            process.refresh_active(entries[:2], empty_snapshot)
        self.assertEqual(marker_reads, [marker_root])
        self.assertEqual([entry.active for entry in entries[:2]], [False, False])

    def test_plugin_contract_errors_name_the_file_and_fail_cli_startup(self):
        variants = [
            (PLUGIN_SOURCE.replace("RUNNER_API_VERSION = 1", "RUNNER_API_VERSION = 2"), "version"),
            ('raise RuntimeError("fixture import exploded")\n', "fixture import exploded"),
            (PLUGIN_SOURCE.replace('name = "harri"', 'name = "copilot"'), "copilot"),
            (PLUGIN_SOURCE.replace('("harri-cli",)', '("copilot",)').replace(
                'return "harri-cli start"', 'return "copilot start"'), "copilot"),
            (PLUGIN_SOURCE.replace("name = \"harri\"", "name = \"harri\"\n    new_session_key = \"A\""), "new_session_key"),
            (PLUGIN_SOURCE.replace("name = \"harri\"", "name = \"harri\"\n    new_session_key = \"ab\""), "new_session_key"),
        ]
        for source, reason in variants:
            with self.subTest(reason=reason):
                self.fixture.plugin.write_text(source, encoding="utf-8")
                with self.assertRaises(RunnerPluginError) as caught:
                    load_registry(self.fixture.plugin_dir)
                self.assertIn(str(self.fixture.plugin), str(caught.exception))
                self.assertIn(reason, str(caught.exception).lower())
                result = subprocess.run(self.fixture.command, env=self.fixture.env, text=True,
                                        capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(self.fixture.plugin), result.stderr)
                self.assertEqual(self.fixture.sessions.read_bytes(), b"")

    def test_unreadable_plugin_directory_is_an_error(self):
        original_iterdir = Path.iterdir

        def iterdir(path):
            if path == self.fixture.plugin_dir:
                raise PermissionError("fixture directory denied")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", iterdir):
            with self.assertRaises(RunnerPluginError) as caught:
                load_registry(self.fixture.plugin_dir)
        self.assertIn(str(self.fixture.plugin_dir), str(caught.exception))
        self.assertIn("denied", str(caught.exception))

    def test_missing_or_empty_external_directory_preserves_builtin_behavior(self):
        missing = self.fixture.home / "no-plugins"
        empty = self.fixture.home / "empty-plugins"
        empty.mkdir()
        for directory in (missing, empty):
            with self.subTest(directory=directory):
                registry = load_registry(directory)
                with patch("session_manager.runner_registry._registry", registry):
                    entry = self.entry("codex resume known Built-in saved session")
                    self.assertTrue(entry.parsed)
                    self.assertEqual((entry.runner, entry.session_id), ("codex", "known"))
                    self.assertEqual(discover_recent("all", runners=("codex",)), [])
        self.assertFalse(missing.exists())

    def test_option_like_description_preserves_launchable_command(self):
        for command in ("omp --resume abc", "unknown --session=abc",
                        "goose session resume --name abc"):
            with self.subTest(command=command):
                line = command + "   --investigate"
                entry = self.entry(line)
                self.assertTrue(entry.parsed)
                self.assertEqual((entry.command, entry.separator, entry.description),
                                 (command, "   ", "--investigate"))
                self.assertEqual(entry.session_id, "abc")
                entry.toggle_done()
                self.assertEqual(entry.serialize(), line + " Done.")


    def test_invalid_custom_ids_and_boundaries_stay_unparsed(self):
        for line in ("harri-cli attach", "harri-cli attach  Description",
                     "harri-cli attach --last Description", "harri-cli attach h-001\tDescription",
                     "harri-cli --resume h-001 Description"):
            with self.subTest(line=line):
                entry = self.entry(line)
                self.assertFalse(entry.parsed)
                self.assertEqual(entry.serialize(), line)
        for command in ('"not-the-input"', '"harri-cli attach h-"', '""'):
            with self.subTest(returned_command=command):
                self.fixture.plugin.write_text(
                    PLUGIN_SOURCE + f'\nRUNNER.match_resume = lambda text: ResumeMatch({command}, "h-001")\n',
                    encoding="utf-8")
                registry = load_registry(self.fixture.plugin_dir)
                with patch("session_manager.runner_registry._registry", registry):
                    self.assertFalse(self.entry().parsed)

    def test_first_id_adopts_discovered_description_without_persisting(self):
        for suffix in ("", " Done.", " Throwaway.", " Done. Throwaway."):
            with self.subTest(suffix=suffix):
                records = self.fixture.home / ".harri" / "sessions"
                (records / "h-002.json").unlink(missing_ok=True)
                app = self.app()
                with patch.object(app, "_choose_harness", return_value="harri"), patch.object(
                    app, "_choose_workdir", return_value=app.startdir
                ), patch.object(app, "_launch_selected"):
                    app._new_session()
                entry = app.document.entries[0]
                if "Done." in suffix:
                    entry.toggle_done()
                if "Throwaway." in suffix:
                    entry.toggle_throwaway()
                with enter_on_stdin():
                    subprocess.run([str(self.fixture.executable), "start"], env=self.fixture.env, check=True)
                app._refresh_new_sessions()
                self.assertEqual(entry.description, "Started fixture" + suffix)
                self.assertEqual(entry.session_id, "h-002")
                self.assertTrue(entry.temporary)
                self.assertEqual(self.fixture.sessions.read_text(), "")
                record = json.loads((records / "h-002.json").read_text())
                record["description"] = "Later prompt"
                (records / "h-002.json").write_text(json.dumps(record))
                app._refresh_new_sessions()
                self.assertEqual(entry.description, "Started fixture" + suffix)

    def test_tracked_new_session_activity_follows_tmux_pane_lifetime(self):
        app = self.app()
        with patch.object(app, "_choose_harness", return_value="harri"), patch.object(
            app, "_choose_workdir", return_value=app.startdir
        ), patch.object(app, "_launch_selected"):
            app._new_session()
        entry = app.document.entries[0]
        pane = SimpleNamespace(pane_id="%42")
        app.new_session_panes[id(entry)] = pane
        with patch("session_manager.process.iter_processes", return_value=()):
            with patch("session_manager.tui.tmux.panes", return_value=[pane]):
                app._refresh_active()
                self.assertEqual(entry.session_id, "")
                self.assertEqual(_status_marker(entry), "A  N")
            with patch("session_manager.tui.tmux.panes", return_value=[]):
                app._refresh_active()
                self.assertEqual(_status_marker(entry), "   N")
                self.assertNotIn(id(entry), app.new_session_panes)

    def test_pending_description_uses_error_color_until_id_adoption(self):
        class DrawingScreen(Screen):
            def __init__(self):
                self.drawn = {}

            def erase(self):
                self.drawn.clear()

            def addnstr(self, y, x, text, width, attr=0):
                self.drawn[text[:width]] = attr

        app = self.app()
        app.stdscr = DrawingScreen()
        app.color_pairs = {"error": 1, "selected": 2, "description": 3}
        with patch.object(app, "_choose_harness", return_value="harri"), patch.object(
            app, "_choose_workdir", return_value=app.startdir
        ), patch.object(app, "_launch_selected"):
            app._new_session()
        entry = app.document.entries[0]
        with patch("session_manager.tui._read_line", return_value="My pending task"), patch(
            "session_manager.tui.time.sleep"
        ):
            app._edit()
        self.assertTrue(app.message_is_error)
        self.assertEqual(self.fixture.sessions.read_text(), "")
        with patch("session_manager.tui.time.monotonic", return_value=1):
            app.last_active_refresh = 1
            app._draw()
            self.assertEqual(app.stdscr.drawn["My pending task"], 1 | curses.A_REVERSE)
            app.selected = -1
            app._draw()
            self.assertEqual(app.stdscr.drawn["My pending task"], 1)
            with enter_on_stdin():
                subprocess.run([str(self.fixture.executable), "start"], env=self.fixture.env, check=True)
            app._refresh_new_sessions()
            app._draw()
            self.assertEqual(app.stdscr.drawn["My pending task"], 2 | curses.A_REVERSE)
            app.selected = -1
            app._draw()
            self.assertEqual(app.stdscr.drawn["My pending task"], 3)
        self.assertFalse(app.message_is_error)
        self.assertEqual(entry.description, "My pending task")
        self.assertEqual(entry.session_id, "h-002")
        self.assertEqual(self.fixture.sessions.read_text(), "harri-cli attach h-002 My pending task")

    def test_new_session_real_launch_adoption_deferred_save_and_attach(self):
        original = self.fixture.sessions.read_bytes()
        app = self.app()
        with patch.object(app, "_choose_harness", return_value="harri"), patch.object(
            app, "_choose_workdir", return_value=app.startdir
        ) as choose_workdir, patch.object(app, "_draw"), patch(
            "session_manager.tui.tmux.inside", return_value=False
        ), patch("session_manager.process.iter_processes", return_value=()), patch(
            "session_manager.tui.curses.def_prog_mode"
        ), patch("session_manager.tui.curses.endwin"), patch(
            "session_manager.tui.curses.reset_prog_mode"
        ), enter_on_stdin():
            app._new_session()
        choose_workdir.assert_called_once_with()
        self.assertEqual(self.fixture.invocations(), [["start"]])
        entry = app.document.entries[0]
        self.assertEqual((entry.runner, entry.session_id, entry.command),
                         ("harri", "h-002", "harri-cli attach h-002"))
        self.assertTrue(entry.temporary)
        self.assertIn("N", _status_marker(entry))
        self.assertEqual(self.fixture.sessions.read_bytes(), original)
        with patch("session_manager.tui._read_line", return_value="Edited session"):
            app._edit()
        self.assertFalse(entry.temporary)
        self.assertNotIn("N", _status_marker(entry))
        self.assertEqual(self.fixture.sessions.read_text(encoding="utf-8"),
                         "harri-cli attach h-002 Edited session")
        self.assertEqual((load_document(self.fixture.sessions).entries[0].runner,
                          load_document(self.fixture.sessions).entries[0].session_id),
                         ("harri", "h-002"))
        attached = subprocess.run([str(self.fixture.executable), "attach", "h-002"],
                                  input="\n", text=True, capture_output=True,
                                  env=self.fixture.env, timeout=10)
        self.assertEqual(attached.returncode, 0, attached.stderr)
        self.assertEqual(attached.stdout.strip(), "attach h-002")
        self.assertEqual(self.fixture.invocations(), [["start"], ["attach", "h-002"]])
        invalid = subprocess.run([str(self.fixture.executable), "attach", "missing"],
                                 input="\n", text=True, capture_output=True,
                                 env=self.fixture.env, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)


if __name__ == "__main__":
    unittest.main()
