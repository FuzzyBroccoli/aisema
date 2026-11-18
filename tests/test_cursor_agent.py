import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from session_manager import process
from session_manager.model import parse_line
from session_manager.runner_registry import load_registry
from session_manager.runners.cursor_agent import RUNNER


class CursorAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aisema-cursor-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.enterContext(patch("session_manager.runners.cursor_agent.Path.home", return_value=self.home))
        registry = load_registry(self.home / "plugins")
        self.enterContext(patch("session_manager.runner_registry._registry", registry))

    def start_process(self, executable, files):
        path = self.home / executable
        path.symlink_to(sys.executable)
        child = subprocess.Popen(
            [str(path), "-c", "import json, os, sys; "
             "files = [open(path) for path in json.loads(os.environ['CURSOR_OPEN_FILES'])]; "
             "print('ready', flush=True); sys.stdin.readline()"],
            env=dict(os.environ, CURSOR_OPEN_FILES=json.dumps([str(path) for path in files])),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        def cleanup():
            if child.poll() is None:
                child.terminate()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        self.assertEqual(child.stdout.readline(), "ready\n")
        return child

    def store(self, session_id, filename="store.db", root=None):
        path = (root or self.home / ".cursor" / "chats") / "project" / session_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        return path

    def test_alias_only_installation_is_available_with_usable_commands(self):
        alias = self.home / "agent"
        alias.symlink_to(sys.executable)
        with patch.dict(os.environ, PATH=str(self.home)):
            self.assertTrue(RUNNER.is_available())
            self.assertEqual(RUNNER.new_session_command(), "agent")
            self.assertEqual(RUNNER.resume_command("chat"), "agent --resume=chat")
            (self.home / "cursor-agent").symlink_to(sys.executable)
            self.assertEqual(RUNNER.new_session_command(), "cursor-agent")
            self.assertEqual(RUNNER.resume_command("chat"), "cursor-agent --resume=chat")
        with patch.dict(os.environ, PATH=str(self.home / "missing")):
            self.assertFalse(RUNNER.is_available())

    def test_alias_process_without_id_owns_exact_chat_and_caches_scan(self):
        store = self.store("chat-123")
        child = self.start_process("agent", [store])
        entries = [parse_line("cursor-agent --resume=chat-123 First", 1),
                   parse_line("agent --resume=chat-123 Alias", 2),
                   parse_line("cursor-agent --resume=chat-12 Wrong prefix", 3),
                   parse_line("copilot --resume chat-123 Other runner", 4)]
        snapshot = process.scan_processes()
        self.assertNotIn("chat-123", snapshot.command_lines[child.pid])
        fd_root = Path("/proc") / str(child.pid) / "fd"
        original_iterdir = Path.iterdir
        scans = []
        def iterdir(path):
            if path == fd_root:
                scans.append(path)
            return original_iterdir(path)
        with patch.object(Path, "iterdir", iterdir):
            process.refresh_active(entries, snapshot)
            process.refresh_active(entries, snapshot)
        self.assertEqual([entry.active for entry in entries], [True, True, False, False])
        self.assertEqual(scans, [fd_root])
        self.assertEqual(RUNNER.find_process("chat-123", snapshot).pid, child.pid)
        child.communicate(input="\n", timeout=5)
        process.refresh_active(entries, process.scan_processes())
        self.assertEqual([entry.active for entry in entries], [False, False, False, False])

    def test_canonical_process_accepts_sqlite_sidecars_but_not_unrelated_paths(self):
        files = [self.store("wal-chat", "store.db-wal"),
                 self.store("shm-chat", "store.db-shm"),
                 self.store("wrong-file", "meta.json"),
                 self.store("wrong-root", root=self.home / "unrelated")]
        child = self.start_process("cursor-agent", files)
        snapshot = process.scan_processes()
        for session_id in ("wal-chat", "shm-chat"):
            with self.subTest(session_id=session_id):
                self.assertEqual(RUNNER.find_process(session_id, snapshot).pid, child.pid)
        for session_id in ("wrong-file", "wrong-root", "missing", ""):
            with self.subTest(session_id=session_id):
                self.assertIsNone(RUNNER.find_process(session_id, snapshot))

    def test_empty_fd_scan_is_cached_and_next_snapshot_sees_new_owner(self):
        store = self.store("new-chat")
        child = self.start_process("agent", [])
        snapshot = process.scan_processes()
        self.assertIsNone(RUNNER.find_process("new-chat", snapshot))
        self.assertEqual(snapshot.runner_data[RUNNER.name], {})
        child.communicate(input="\n", timeout=5)
        (self.home / "agent").unlink()
        owner = self.start_process("agent", [store])
        self.assertIsNone(RUNNER.find_process("new-chat", snapshot))
        self.assertEqual(RUNNER.find_process("new-chat", process.scan_processes()).pid, owner.pid)

    def test_other_executable_opening_chat_does_not_claim_cursor_ownership(self):
        child = self.start_process("unrelated", [self.store("shared")])
        self.assertIsNone(RUNNER.find_process("shared", process.scan_processes()))
        self.assertIsNone(child.poll())


if __name__ == "__main__":
    unittest.main()
