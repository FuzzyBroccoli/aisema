import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from session_manager.config import Config, DEFAULTS
from session_manager.model import SessionEntry
from session_manager.process import ProcessMatch, ProcessSnapshot
from session_manager.tmux import Pane
from session_manager.runner_api import DiscoveredSession, NativeFork, Runner
from session_manager.storage import load_document
from session_manager.tui import SessionApp


class FixtureRunner(Runner):
    def __init__(self, name, path):
        self.name = name
        self.command_names = (name,)
        self.path = path
        self.manual = False

    def is_available(self):
        return True

    def discover(self):
        return [DiscoveredSession(self.path, 'saved', 'Original', 0, self.name, f'{self.name} --resume saved')]

    def native_fork(self, session_id):
        return NativeFork((self.name, '--resume', session_id), '/fork' if self.manual else '')

    def context_command(self, path):
        return (self.name, str(path))


class ForkPopupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='aisema-fork-ui-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.dict(os.environ, XDG_DATA_HOME=str(self.root)))
        self.plan = self.root / 'Implementation PLAN.md'
        self.plan.write_text('# Actual plan\nFinish the remaining step.\n')
        self.note = self.root / 'Notes.md'
        self.note.write_text('# Reference notes\n')
        source_path = self.root / 'source.jsonl'
        source_path.write_text('\n'.join(json.dumps(record) for record in [
            {'type': 'session', 'cwd': str(self.root)},
            {'role': 'user', 'content': 'Review Notes.md'},
            {'role': 'assistant', 'content': 'Full-session-only decision', 'tool_calls': [
                {'name': 'write', 'arguments': {'path': self.plan.name, 'content': '# Plan'}}]},
        ]))
        self.source = FixtureRunner('source', source_path)
        self.destination = FixtureRunner('destination', source_path)
        runners = {'source': self.source, 'destination': self.destination}
        registry = SimpleNamespace(
            get=runners.get, all=lambda: (self.destination, self.source),
            for_command=lambda command: runners.get(command.split()[0]) if command.split() else None,
        )
        self.enterContext(patch('session_manager.runner_registry._registry', registry))
        self.path = self.root / 'sessions.txt'
        self.path.write_text('destination --resume other Other\nsource --resume saved Original\n')
        document = load_document(self.path)
        screen = SimpleNamespace(getmaxyx=lambda: (24, 100))
        with patch.object(SessionApp, '_setup_colors'):
            self.app = SessionApp(screen, document, Config(DEFAULTS, self.root / 'config'))
        self.app.selected = 1

    def test_cancel_at_each_transfer_stage_has_no_launch_or_session_side_effects(self):
        original = self.path.read_bytes()
        for cancel_title in ('Fork session', 'Fork destination', 'Session context'):
            with self.subTest(stage=cancel_title):
                def choose(title, options, selected=0):
                    if title == cancel_title:
                        return None
                    return 1 if title == 'Fork session' else 0
                with patch.object(self.app, '_choose_option', side_effect=choose), patch.object(
                    self.app, '_run_session_command'
                ) as launch:
                    self.app._fork()
                launch.assert_not_called()
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(len(self.app.document.entries), 2)
                self.assertEqual(list(self.root.rglob('job.json')), [])
                self.assertFalse(self.app.message_is_error)

    def test_markdown_plan_precedes_mentions_and_default_destination_is_source(self):
        defaults = []
        def choose(title, options, selected=0):
            if title == 'Fork session':
                self.assertEqual(options[:2], ['Native fork', 'Transfer session to another runner'])
                self.assertTrue(options[2].startswith('Plan: Implementation PLAN.md'))
                self.assertTrue(options[3].startswith('Markdown: Notes.md'))
                return 2
            self.assertEqual(title, 'Fork destination')
            defaults.append(options[selected])
            return selected
        with patch.object(self.app, '_choose_option', side_effect=choose), patch.object(
            self.app, '_run_session_command'
        ):
            self.app._fork()
        self.assertEqual(defaults, ['source'])
        job, = self.root.rglob('job.json')
        data = json.loads(job.read_text())
        self.assertEqual(data['target_runner'], 'source')
        self.assertFalse(data['compress'])
        context = (job.parent / 'context.md').read_text()
        self.assertIn(self.plan.read_text(), context)
        self.assertNotIn('Full-session-only decision', context)
        self.assertEqual(self.path.read_text(), 'destination --resume other Other\nsource --resume saved Original\n')

    def test_manual_native_fork_confirmation_can_cancel_without_resuming_source(self):
        self.source.manual = True
        with patch.object(self.app, '_choose_option', side_effect=[0, None]), patch.object(
            self.app, '_run_session_command'
        ) as launch:
            self.app._fork()
        launch.assert_not_called()
        self.assertEqual(list(self.root.rglob('job.json')), [])
        self.assertEqual(len(self.app.document.entries), 2)

    def test_native_child_parent_id_in_argv_does_not_claim_original_activity(self):
        parent = self.app.document.entries[1]
        child = SessionEntry("", "source --resume child", " ", "Child", 3, source="temporary")
        self.app.document.add_temporary(child)
        pane = Pane("%42", 4200, "@42", "main")
        self.app.new_session_panes[id(child)] = pane
        self.app.native_fork_entries.add(id(child))
        fork_process = ProcessMatch(4201, ("source", "fork", "saved"))
        original_process = ProcessMatch(10, ("source", "--resume", "saved"))
        for original_running in (False, True):
            with self.subTest(original_running=original_running):
                matches = (fork_process, original_process) if original_running else (fork_process,)
                snapshot = ProcessSnapshot(matches, {"source": matches}, {
                    item.pid: " ".join(item.argv) for item in matches
                })
                with patch("session_manager.tui.process.scan_processes", return_value=snapshot), patch(
                    "session_manager.tui.tmux._is_descendant", side_effect=lambda pid, root: pid == 4201
                ), patch("session_manager.tui.tmux.panes", return_value=[pane]):
                    self.app._refresh_active()
                self.assertEqual(parent.active, original_running)
                self.assertTrue(child.active)

    def test_find_command_still_filters_sessions(self):
        self.app._run_command(':find Original')
        self.assertEqual([entry.session_id for entry in self.app.visible], ['saved'])
        self.app._run_command(':find')
        self.assertEqual(len(self.app.visible), 2)


if __name__ == '__main__':
    unittest.main()
