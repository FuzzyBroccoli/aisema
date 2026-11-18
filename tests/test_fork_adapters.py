from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from session_manager.runners.codex import RUNNER as CODEX
from session_manager.runners.copilot import RUNNER as COPILOT
from session_manager.runners.cursor_agent import RUNNER as CURSOR
from session_manager.runners.goose import RUNNER as GOOSE
from session_manager.runners.omp import RUNNER as OMP
from session_manager.runners.opencode import RUNNER as OPENCODE


# Documented help layouts: commands can be executable-prefixed (yargs) or bare.
OPENCODE_V1 = "Commands:\n  opencode run [message..]  Run a message\n  opencode export [sessionID]  Export session\nOptions:\n  --session  Session ID\n  --fork  Fork the session\n"
OPENCODE_V2 = "Commands:\n  mini     Interactive terminal\n  run      Run a message\n  session  Manage sessions\nOptions:\n  --session  Session ID\n"
GOOSE_EXPORT = "Usage: goose session export [OPTIONS]\n  --session-id <SESSION_ID>\n  --format <FORMAT> [default: markdown]\n  --output <OUTPUT>\n"
GOOSE_RUN = "--interactive --instructions --no-profile --no-session --quiet --output-format"


class ForkAdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aisema-adapters-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch("pathlib.Path.home", return_value=self.root))
        self.enterContext(patch.dict(os.environ, {
            "CODEX_HOME": str(self.root / ".codex"),
            "PI_CODING_AGENT_DIR": str(self.root / ".omp" / "agent"),
            "COPILOT_HOME": str(self.root / ".copilot"),
        }))

    def store(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    @contextmanager
    def cli(self, outputs):
        calls = []

        def run(argv, **kwargs):
            argv = tuple(argv)
            calls.append(argv)
            self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
            if argv not in outputs:
                return subprocess.CompletedProcess(argv, 2, "", "unsupported command")
            value = outputs[argv]
            if isinstance(value, tuple):
                status, stdout, stderr = value
                return subprocess.CompletedProcess(argv, status, stdout, stderr)
            return subprocess.CompletedProcess(argv, 0, value, "")

        with patch("session_manager.runner_commands.subprocess.run", side_effect=run):
            yield calls

    def test_opencode_export_version_selection_preserves_assistant_and_tool_payloads(self):
        session_id = "ses_with spaces;not-shell"
        export = json.dumps({
            "info": {"id": session_id, "directory": "/work/source project"},
            "messages": [
                {"info": {"id": "u", "role": "user"},
                 "parts": [{"type": "text", "text": "Implement the plan"}]},
                {"info": {"id": "a", "role": "assistant"}, "parts": [
                    {"type": "text", "text": "Decision: preserve the migration"},
                    {"type": "tool", "tool": "read", "state": {
                        "status": "completed", "input": {"filePath": "plan.md"},
                        "content": "start\n" + "full tool content\n" * 2000 + "end"}},
                ]},
            ],
        }, indent=2)
        for help_text, argv in (
            (OPENCODE_V1, ("opencode", "export", session_id)),
            (OPENCODE_V2, ("opencode", "session", "export", session_id)),
        ):
            with self.subTest(version=help_text), self.cli({
                ("opencode", "--help"): help_text,
                ("opencode", "session", "export", "--help"): "Export session as JSON",
                argv: export,
            }) as calls:
                content = OPENCODE.session_content(session_id)
                self.assertEqual(content.text, export)
                self.assertEqual(content.cwd, Path("/work/source project"))
                self.assertIn(argv, calls)

    def test_opencode_native_fork_selects_supported_interactive_frontend(self):
        cases = [
            (OPENCODE_V1, "", ("opencode", "--session", "ses_exact", "--fork")),
            (OPENCODE_V2, "--session --fork --prompt", ("opencode", "mini", "--session", "ses_exact", "--fork")),
            (OPENCODE_V2, "--session --prompt", None),
        ]
        for root_help, mini_help, expected in cases:
            with self.subTest(expected=expected), self.cli({
                ("opencode", "--help"): root_help,
                ("opencode", "mini", "--help"): mini_help,
            }):
                fork = OPENCODE.native_fork("ses_exact")
                self.assertEqual(None if fork is None else fork.argv, expected)
                if fork is not None:
                    self.assertEqual(fork.instruction, "")

    def test_export_errors_are_actionable_not_prompt_only_fallbacks(self):
        for runner, help_argv, export_argv, help_text in (
            (OPENCODE, ("opencode", "--help"), ("opencode", "export", "id"), OPENCODE_V1),
            (GOOSE, ("goose", "session", "export", "--help"),
             ("goose", "session", "export", "--session-id", "id", "--format", "json"), GOOSE_EXPORT),
        ):
            for response, message in (
                ((1, "", "session not found: id"), "session not found"),
                ("not JSON", "JSON"),
                ("{}", "empty or invalid"),
            ):
                with self.subTest(runner=runner.name, response=response), self.cli({
                    help_argv: help_text, export_argv: response,
                }):
                    with self.assertRaisesRegex(ValueError, message):
                        runner.session_content("id")
        with self.cli({}):
            with self.assertRaisesRegex(ValueError, "update OpenCode"):
                OPENCODE.session_content("id")
            with self.assertRaisesRegex(ValueError, "update Goose"):
                GOOSE.session_content("id")

    def test_goose_json_export_and_legacy_jsonl_are_complete(self):
        export = json.dumps({
            "id": "goose exact id", "working_dir": "/work/goose",
            "conversation": {"messages": [
                {"role": "user", "content": [{"type": "text", "text": "goal"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "decision"}]},
                {"role": "user", "content": [{"type": "toolResponse", "toolResult": {
                    "content": [{"type": "text", "text": "complete result"}]}}]},
            ]},
        })
        with self.cli({
            ("goose", "session", "export", "--help"): GOOSE_EXPORT,
            ("goose", "session", "export", "--session-id", "goose exact id", "--format", "json"): export,
        }):
            content = GOOSE.session_content("goose exact id")
            self.assertEqual(content.text, export)
            self.assertEqual(content.cwd, Path("/work/goose"))
        legacy = '{"id":"old","cwd":"/work/old","role":"user","content":"goal"}\n{"role":"assistant","content":"answer"}\n'
        self.store(".config/goose/sessions/old.jsonl", legacy)
        with self.cli({}):
            self.assertEqual(GOOSE.session_content("old").text, legacy)

    def test_manual_native_fallbacks_are_never_plain_resumes(self):
        with self.cli({("codex", "--help"): "Commands:\n  resume  Resume a session\n"}):
            self.assertIsNone(CODEX.native_fork("source"))
        with self.cli({("codex", "--help"): "Commands:\n  fork  Fork a session\n"}):
            fork = CODEX.native_fork("source")
            self.assertEqual(fork.argv, ("codex", "fork", "source"))
            self.assertEqual(fork.instruction, "")
        with self.cli({("omp", "--help"): "--resume <id> --forked-session-info"}):
            fork = OMP.native_fork("source with spaces")
            self.assertEqual(fork.instruction, "/fork")
            self.assertEqual(fork.argv[-1], "source with spaces")
        with self.cli({("omp", "--help"): "--resume <id> --fork <id|path>"}):
            fork = OMP.native_fork("/source dir/session.jsonl")
            self.assertEqual(fork.instruction, "")
            self.assertEqual(fork.argv[-2:], ("--fork", "/source dir/session.jsonl"))
        with patch("session_manager.runners.cursor_agent.shutil.which", side_effect=lambda name: "/bin/agent" if name == "agent" else None):
            fork = CURSOR.native_fork("chat-id")
            self.assertEqual(fork.argv[0], "agent")
            self.assertEqual(fork.instruction, "/fork")
        self.assertEqual(COPILOT.native_fork("id").instruction, "/fork")
        with self.cli({("goose", "session", "--help"): "--resume --session-id"}):
            self.assertIsNone(GOOSE.native_fork("id"))
        with self.cli({("goose", "session", "--help"): "--resume --session-id --fork"}):
            fork = GOOSE.native_fork("id with spaces")
            self.assertIn("--fork", fork.argv)
            self.assertIn("--resume", fork.argv)
            self.assertEqual(fork.argv[-1], "id with spaces")

    def test_codex_and_omp_load_complete_journals_not_discovery_prompts(self):
        session_id = "12345678-1234-1234-1234-123456789abc"
        cases = (
            (CODEX, f".codex/sessions/rollout-2026-01-02T03-04-05-{session_id}.jsonl", [
                {"type": "session_meta", "payload": {"id": session_id, "cwd": "/work/codex"}},
                {"type": "response_item", "payload": {"role": "user", "content": "goal"}},
                {"type": "response_item", "payload": {"role": "assistant", "content": "decision"}},
                {"type": "response_item", "payload": {"type": "function_call_output", "output": "full tool result"}},
            ], "/work/codex"),
            (OMP, f".omp/agent/sessions/project/2026-01-02_{session_id}.jsonl", [
                {"type": "session", "id": session_id, "cwd": "/work/omp"},
                {"type": "message", "message": {"role": "user", "content": "goal"}},
                {"type": "message", "message": {"role": "assistant", "content": "decision"}},
                {"type": "message", "message": {"role": "toolResult", "content": "full tool result"}},
            ], "/work/omp"),
        )
        for runner, path, records, cwd in cases:
            with self.subTest(runner=runner.name):
                raw = "".join(json.dumps(record) + "\n" for record in records)
                self.store(path, raw)
                content = runner.session_content(session_id)
                self.assertEqual(content.text, raw)
                self.assertEqual(content.cwd, Path(cwd))

    def test_copilot_prefers_full_modern_events_and_retains_legacy_fallback(self):
        self.store(".copilot/sessions/source.json", '{"id":"source","messages":[{"role":"user","content":"legacy prompt"}]}')
        events = '\n'.join(json.dumps(event) for event in [
            {"type": "session.start", "data": {"context": {"cwd": "/work/copilot"}}},
            {"type": "user.message", "data": {"content": "new goal"}},
            {"type": "assistant.message", "data": {"content": "assistant decision"}},
            {"type": "tool.execution_complete", "data": {"toolCallId": "call", "result": {"content": "full tool output"}}},
        ]) + '\n'
        modern = self.store(".copilot/session-state/source/events.jsonl", events)
        content = COPILOT.session_content("source")
        self.assertEqual(content.text, events)
        self.assertEqual(content.cwd, Path("/work/copilot"))
        modern.unlink()
        self.assertIn("legacy prompt", COPILOT.session_content("source").text)

    def test_cursor_uses_exact_chat_metadata_only_when_transcript_has_no_cwd(self):
        transcript = '{"role":"user","content":"goal"}\n{"role":"assistant","content":"answer"}\n'
        self.store(".cursor/projects/project/agent-transcripts/chat[1]/chat[1].jsonl", transcript)
        self.store(".cursor/chats/project/chat1/meta.json", '{"cwd":"/wrong/prefix"}')
        self.store(".cursor/chats/project/chat[1]/meta.json", '{"workingDirectory":"/work/cursor"}')
        content = CURSOR.session_content("chat[1]")
        self.assertEqual(content.text, transcript)
        self.assertEqual(content.cwd, Path("/work/cursor"))
        with_cwd = '{"cwd":"/authoritative/transcript"}\n' + transcript
        self.store(".cursor/projects/project/agent-transcripts/chat[1]/chat[1].jsonl", with_cwd)
        self.assertEqual(CURSOR.session_content("chat[1]").cwd, Path("/authoritative/transcript"))

    def test_fresh_context_keeps_transcript_out_of_argv_and_omp_autoresume_off(self):
        context = self.store("handoff folder/full context.md", "complete data\n" * 50000)
        with self.cli({("goose", "run", "--help"): GOOSE_RUN}), patch("session_manager.runners.cursor_agent.shutil.which", return_value="/bin/agent"):
            for runner in (CODEX, COPILOT, CURSOR, GOOSE, OMP, OPENCODE):
                with self.subTest(runner=runner.name):
                    argv = runner.context_command(context)
                    self.assertIsNotNone(argv)
                    self.assertLess(sum(map(len, argv)), 2048)
                    self.assertTrue(any(str(context) in argument for argument in argv))
                    self.assertFalse(any(option in argv for option in ("--resume", "--continue", "--print")))
            omp_argv = OMP.context_command(context)
            overlay = Path(omp_argv[omp_argv.index("--config") + 1])
            self.assertEqual(overlay.read_text(), "autoResume: false\n")
            self.assertEqual(overlay.stat().st_mode & 0o777, 0o600)
            self.assertIn("--interactive", GOOSE.context_command(context))
            self.assertIn("--attachment", COPILOT.context_command(context))
        absent = self.root / "absent.md"
        OMP.context_command(absent)
        self.assertFalse(absent.with_name(absent.name + ".omp.yml").exists())

    def test_fresh_summary_modes_do_not_reference_source_and_v2_uses_private_config(self):
        output = self.root / "summary.md"
        with self.cli({("goose", "run", "--help"): GOOSE_RUN}), patch("session_manager.runners.cursor_agent.shutil.which", side_effect=lambda name: "/bin/agent" if name == "agent" else None):
            for runner in (CODEX, COPILOT, CURSOR, GOOSE, OMP):
                with self.subTest(runner=runner.name):
                    command = runner.summary_command(output)
                    self.assertFalse(any(option in command.argv for option in ("--resume", "--continue", "--session", "--fork")))
            self.assertNotIn("-p", COPILOT.summary_command(output).argv)
            self.assertEqual(CODEX.summary_command(output).output_file, output)
            self.assertEqual(GOOSE.summary_command(output).output_format, "goose-json")
            self.assertEqual(CURSOR.summary_command(output).argv[0], "agent")
        for v2 in (False, True):
            with self.subTest(v2=v2), self.cli({
                ("opencode", "run", "--help"): "--agent --format" + (" --standalone" if v2 else ""),
            }):
                command = OPENCODE.summary_command(output)
                config = json.loads((command.cwd / "opencode.json").read_text())
                self.assertEqual(command.output_format, "opencode-json")
                self.assertEqual("--standalone" in command.argv, v2)
                if v2:
                    self.assertEqual(config["agents"]["aisema-summary"]["permissions"],
                                     [{"action": "*", "resource": "*", "effect": "deny"}])
                else:
                    self.assertEqual(config["agent"]["aisema-summary"]["permission"], {"*": "deny"})
                    self.assertTrue(command.argv[1].startswith("OPENCODE_CONFIG_CONTENT="))
        with self.cli({}):
            with self.assertRaisesRegex(ValueError, "update Goose"):
                GOOSE.summary_command(output)
            with self.assertRaisesRegex(ValueError, "update OpenCode"):
                OPENCODE.summary_command(output)


if __name__ == "__main__":
    unittest.main()
