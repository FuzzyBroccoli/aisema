import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from session_manager.runner_api import DiscoveredSession, SessionContent
from session_manager.session_context import (
    MarkdownFile,
    content_from_text,
    load_session_content,
    markdown_files,
)


class SessionContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="aisema-content-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def discovered(self, path, session_id="exact"):
        return DiscoveredSession(path, session_id, "Description", 0, "fixture", "fixture --resume exact")

    def content(self, records, cwd=None):
        return content_from_text("\n".join(json.dumps(record) for record in records), cwd)

    def test_exact_session_load_preserves_complete_assistant_and_tool_data_verbatim(self):
        records = [
            {"type": "session_meta", "payload": {"cwd": str(self.root)}},
            {"role": "user", "content": "Original question"},
            {"role": "assistant", "content": "Full assistant answer — " + "detail " * 20000},
            {"type": "function_call", "name": "write", "arguments": {"path": "Plan.md", "content": "Complete file"}},
            {"role": "tool", "content": "Complete tool response, not just user prompts"},
        ]
        raw = ("\r\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\r\n").encode("utf-8")
        source = self.root / "source.jsonl"
        source.write_bytes(raw)
        prefix = self.root / "wrong.jsonl"
        prefix.write_text('{"content":"wrong session"}', encoding="utf-8")
        sessions = (item for item in [self.discovered(prefix, "exact-prefix"), self.discovered(source)])
        result = load_session_content(sessions, "exact")
        self.assertEqual(result, SessionContent(raw.decode("utf-8"), self.root))
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(markdown_files(result), (MarkdownFile(self.root / "Plan.md", True),))

    def test_metadata_cwd_is_nested_source_metadata_and_explicit_cwd_wins(self):
        variants = [
            {"type": "session", "cwd": str(self.root)},
            {"type": "session_meta", "payload": {"meta": {"directory": str(self.root)}}},
            {"working_dir": str(self.root)},
            {"metadata": {"workingDirectory": str(self.root)}},
            {"type": "session.start", "data": {"context": {"cwd": str(self.root)}}},
            {"metadata": {"context": str(self.root)}},
        ]
        for metadata in variants:
            with self.subTest(metadata=metadata):
                text = json.dumps([metadata, {"role": "assistant", "content": "Read `docs/Notes.md`"}])
                self.assertEqual(content_from_text(text).cwd, self.root)
                self.assertEqual(markdown_files(content_from_text(text)), (MarkdownFile(self.root / "docs/Notes.md", False),))
                explicit = self.root / "explicit"
                overridden = content_from_text(text, explicit)
                self.assertEqual(overridden.cwd, explicit)
                self.assertEqual(markdown_files(overridden), (MarkdownFile(explicit / "docs/Notes.md", False),))

    def test_only_written_plans_are_prioritized_with_stable_remaining_order(self):
        content = self.content([
            {"role": "user", "content": "Read README.md and `docs/Future PLAN.md`"},
            {"role": "assistant", "content": "I created `docs/Claimed Plan.md`"},
            {"type": "toolCall", "name": "write", "arguments": {"path": "docs/actual pLaN.MD", "content": "steps"}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "docs/notes.md", "content": "notes"}},
            {"type": "function_call", "name": "create_file", "arguments": json.dumps({"path": "Second-PLAN.md"})},
        ], self.root)
        self.assertEqual(markdown_files(content), (
            MarkdownFile(self.root / "docs/actual pLaN.MD", True),
            MarkdownFile(self.root / "Second-PLAN.md", True),
            MarkdownFile(self.root / "README.md", False),
            MarkdownFile(self.root / "docs/Future PLAN.md", False),
            MarkdownFile(self.root / "docs/Claimed Plan.md", False),
            MarkdownFile(self.root / "docs/notes.md", True),
        ))

    def test_normalized_paths_deduplicate_and_later_write_upgrades_evidence(self):
        content = self.content([
            {"role": "assistant", "content": "`docs/../Plan.md` README.md"},
            {"role": "tool", "content": f"Reviewed `{self.root / 'Plan.md'}`"},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "./Plan.md"}},
            {"role": "user", "content": "[same](./docs/../Plan.md#steps)"},
        ], self.root)
        self.assertEqual(markdown_files(content), (
            MarkdownFile(self.root / "Plan.md", True),
            MarkdownFile(self.root / "README.md", False),
        ))

    def test_markdown_links_backticks_explicit_paths_spaces_and_url_exclusions(self):
        content = self.content([
            {"role": "assistant", "content": (
                '[notes](<docs/My Notes.Md>) `docs/Other Notes.md` '
                '[encoded](docs/Encoded%20Name.md#section) '
                '[titled](docs/Title.md "A title") '
                '[remote](https://example.org/Remote.md) '
                '`https://example.org/Code.md` https://example.org/Bare.md '
                '[mail](mailto:person@example.org.md)'
            )},
            {"type": "tool_result", "result": {"file_path": "docs/Tool Result.md"}},
            {"role": "tool", "content": 'Read "docs/Quoted Name.md" and docs/plain.md:12'},
            {"path": "https://example.org/Explicit.md"},
        ], self.root)
        self.assertEqual(markdown_files(content), tuple(
            MarkdownFile(self.root / name, False) for name in (
                "docs/My Notes.Md", "docs/Other Notes.md", "docs/Encoded Name.md",
                "docs/Title.md", "docs/Tool Result.md", "docs/Quoted Name.md", "docs/plain.md",
            )
        ))

    def test_apply_patch_additions_and_explicit_creation_events_are_evidence(self):
        patch_text = "*** Begin Patch\n*** Add File: docs/Patch Plan.md\n+steps\n*** Update File: README.md\n@@\n+change\n*** End Patch"
        content = self.content([
            {"type": "function_call", "name": "apply_patch", "arguments": patch_text},
            {"type": "file_created", "path": "Created.md"},
            {"type": "file_written", "path": "Written.md"},
            {"type": "file_change", "changes": [{"kind": "add", "path": "Added.md"}, {"kind": "delete", "path": "Deleted.md"}]},
            {"type": "tool_use", "name": "text_editor", "input": {"command": "write", "path": "Editor.md"}},
            {"role": "assistant", "content": "*** Add File: docs/Prose Plan.md\nI only mentioned this patch."},
        ], self.root)
        self.assertEqual(markdown_files(content), (
            MarkdownFile(self.root / "docs/Patch Plan.md", True),
            MarkdownFile(self.root / "README.md", False),
            MarkdownFile(self.root / "Created.md", True),
            MarkdownFile(self.root / "Written.md", True),
            MarkdownFile(self.root / "Added.md", True),
            MarkdownFile(self.root / "Deleted.md", False),
            MarkdownFile(self.root / "Editor.md", True),
            MarkdownFile(self.root / "docs/Prose Plan.md", False),
        ))

    def test_copilot_committed_file_edits_not_permissions_or_started_calls_create_plans(self):
        committed = self.root / "Committed PLAN.md"
        edited = self.root / "Edited Plan.md"
        deleted = self.root / "Deleted Plan.md"
        content = self.content([
            {"type": "session.start", "data": {"context": {"cwd": str(self.root)}}},
            {"type": "permission.requested", "data": {
                "toolName": "create_file", "arguments": {"path": "Requested Plan.md", "action": "create"},
            }},
            {"type": "tool.execution_start", "data": {
                "toolName": "create_file", "arguments": {"path": "Started Plan.md"},
            }},
            {"type": "tool.execution_complete", "data": {"fileEdits": [
                {"kind": "create", "path": str(committed)},
                {"kind": "edit", "path": str(edited)},
                {"kind": "delete", "path": str(deleted)},
            ]}},
        ])
        self.assertEqual(markdown_files(content), (
            MarkdownFile(committed, True),
            MarkdownFile(self.root / "Requested Plan.md", False),
            MarkdownFile(self.root / "Started Plan.md", False),
            MarkdownFile(edited, False),
            MarkdownFile(deleted, False),
        ))

    def test_opencode_completed_write_state_input_is_evidence_but_failed_or_pending_is_not(self):
        content = self.content([
            {"type": "tool", "tool": "write", "state": {
                "status": "pending", "input": {"filePath": "Pending Plan.md"},
            }},
            {"type": "tool", "tool": "write", "state": {
                "status": "error", "input": {"filePath": "Failed Plan.md"},
            }},
            {"type": "tool", "tool": "read", "state": {
                "status": "completed", "input": {"filePath": "Read Plan.md"},
            }},
            {"type": "tool", "tool": "write", "state": {
                "status": "completed", "input": {"filePath": "Written Plan.md", "content": "Steps"},
            }},
        ], self.root)
        self.assertEqual(markdown_files(content), (
            MarkdownFile(self.root / "Written Plan.md", True),
            MarkdownFile(self.root / "Pending Plan.md", False),
            MarkdownFile(self.root / "Failed Plan.md", False),
            MarkdownFile(self.root / "Read Plan.md", False),
        ))

    def test_missing_cwd_does_not_resolve_relative_paths_against_application_cwd(self):
        absolute = self.root / "Missing.md"
        for metadata in ({}, {"cwd": "relative/source"}):
            with self.subTest(metadata=metadata):
                content = self.content([metadata, {"content": f"relative.md `{absolute}`"}])
                self.assertIsNone(content.cwd)
                self.assertEqual(markdown_files(content), (MarkdownFile(absolute, False),))
        self.assertEqual(markdown_files(SessionContent("relative.md", Path("relative"))), ())
        self.assertFalse(absolute.exists())

    def test_only_referenced_files_are_listed_without_scanning_workspace(self):
        (self.root / "Unrelated Plan.md").write_text("private unrelated file", encoding="utf-8")
        content = content_from_text("Read `missing/Referenced.md`", self.root)
        self.assertEqual(markdown_files(content), (MarkdownFile(self.root / "missing/Referenced.md", False),))

    def test_unavailable_session_path_and_missing_source_errors(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            load_session_content([self.discovered(None, "exact-prefix")], "exact")
        with self.assertRaisesRegex(ValueError, "must export complete content"):
            load_session_content([self.discovered(None)], "exact")
        with self.assertRaises(FileNotFoundError):
            load_session_content([self.discovered(self.root / "missing.jsonl")], "exact")

    def test_binary_sqlite_invalid_and_empty_sources_are_not_used_as_context(self):
        cases = [
            ("binary.jsonl", b"\xff\xfe", "not UTF-8"),
            ("nul.jsonl", b'{"data":"\x00"}', "binary data"),
            ("hidden.jsonl", b"SQLite format 3\x00other data", "owning runner"),
            ("store.db", b"SQLite format 3\x00", "owning runner"),
            ("invalid.jsonl", b'{"valid":true}\nnot valid JSON\n', "line 2"),
            ("empty.jsonl", b"", "empty or scalar"),
            ("scalar.json", b'"not a complete session"', "empty or scalar"),
        ]
        for name, data, message in cases:
            with self.subTest(name=name):
                source = self.root / name
                source.write_bytes(data)
                with self.assertRaisesRegex(ValueError, message):
                    load_session_content([self.discovered(source)], "exact")
                self.assertEqual(source.read_bytes(), data)

    def test_zstd_unavailable_is_actionable_and_never_reads_compressed_bytes_as_text(self):
        source = self.root / "session.jsonl.zst"
        source.write_bytes(b"\x28\xb5\x2f\xfdcompressed")
        with patch("session_manager.session_context.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Install zstd"):
                load_session_content([self.discovered(source)], "exact")

    @unittest.skipUnless(shutil.which("zstd"), "zstd is not installed")
    def test_actual_zstd_binary_exports_complete_context_and_rejects_bad_compression(self):
        raw = (json.dumps({"cwd": str(self.root)}) + '\n{"role":"assistant","content":"Complete answer"}\n').encode()
        compressed = subprocess.run([shutil.which("zstd"), "-q", "-c"], input=raw, capture_output=True, check=True).stdout
        source = self.root / "source.jsonl.zst"
        source.write_bytes(compressed)
        self.assertEqual(load_session_content([self.discovered(source)], "exact"), SessionContent(raw.decode(), self.root))
        self.assertEqual(source.read_bytes(), compressed)
        source.write_bytes(b"not compressed")
        with self.assertRaisesRegex(ValueError, "Cannot decompress"):
            load_session_content([self.discovered(source)], "exact")
