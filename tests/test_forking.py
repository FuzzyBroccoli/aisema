import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from session_manager.forking import prepare_job, run_job, summary_text
from session_manager.runner_api import SessionContent, SummaryCommand


class ForkingTests(unittest.TestCase):
    def test_opencode_uses_only_final_assistant_message_and_deduplicates_parts(self):
        events = [
            {"type": "text", "part": {"messageID": "first", "id": "a", "text": "Exploration, not the handoff"}},
            {"type": "tool_use", "part": {"text": "Tool payload must not leak into summary"}},
            {"type": "text", "part": {"messageID": "final", "id": "b", "text": "Goal and constraints"}},
            {"type": "text", "part": {"messageID": "final", "id": "b", "text": "Goal and constraints"}},
            {"type": "text", "part": {"messageID": "final", "id": "c", "text": "Next steps"}},
            {"type": "step_finish"},
        ]
        result = summary_text("\n".join(json.dumps(event) for event in events), SummaryCommand((), "opencode-json"))
        self.assertEqual(result, "Goal and constraints\nNext steps\n")
        events.append({"type": "error", "error": "provider failed"})
        with self.assertRaisesRegex(ValueError, "provider failed"):
            summary_text("\n".join(json.dumps(event) for event in events), SummaryCommand((), "opencode-json"))

    def test_goose_requires_completion_and_uses_last_assistant_text_only(self):
        result = {
            "metadata": {"status": "completed"},
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Raw transcript"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Earlier answer"}]},
                {"role": "assistant", "content": [
                    {"type": "thinking", "text": "Private reasoning"},
                    {"type": "text", "text": "Handoff"},
                ]},
            ],
        }
        self.assertEqual(summary_text(json.dumps(result), SummaryCommand((), "goose-json")), "Handoff\n")
        result["metadata"]["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "did not complete"):
            summary_text(json.dumps(result), SummaryCommand((), "goose-json"))

    def test_summary_file_has_precedence_over_progress_stdout_and_empty_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.md"
            output.write_text("Actual final handoff\n")
            self.assertEqual(
                summary_text("diagnostics and progress", SummaryCommand((), output_file=output)),
                "Actual final handoff\n",
            )
            output.write_text("")
            with self.assertRaisesRegex(ValueError, "no handoff"):
                summary_text("progress is not a handoff", SummaryCommand((), output_file=output))
        with self.assertRaisesRegex(ValueError, "no handoff"):
            summary_text("  \n", SummaryCommand(()))

    def test_failed_source_summary_never_starts_destination_or_changes_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "source.jsonl"
            original.write_text('{"role":"assistant","content":"Verified result and unresolved error"}\n')
            before = original.read_bytes()
            destination_marker = root / "destination-started"
            source = SimpleNamespace(
                name="source", is_available=lambda: True,
                summary_command=lambda output: SummaryCommand((
                    sys.executable, "-c", "import sys; sys.stderr.write('authentication failed\\n'); sys.exit(7)"
                )),
            )
            target = SimpleNamespace(
                name="target", is_available=lambda: True,
                context_command=lambda context: (sys.executable, "-c", f"open({str(destination_marker)!r}, 'w').close()"),
            )
            registry = SimpleNamespace(get=lambda name: {"source": source, "target": target}.get(name))
            with patch.dict(os.environ, XDG_DATA_HOME=str(root)):
                job = prepare_job("source", "original-id", "target", SessionContent(original.read_text(), root), compress=True)
            self.assertEqual(stat.S_IMODE(job.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(job.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((job.parent / "context.md").stat().st_mode), 0o600)
            with patch("session_manager.forking.get_registry", return_value=registry), patch(
                "session_manager.forking.sys.stdin", new=io.StringIO()
            ), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_job(str(job)), 1)
            self.assertFalse(destination_marker.exists())
            self.assertEqual(original.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
