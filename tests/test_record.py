"""Tests for hook → trace recording (Phase 1a / 1b)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace import registry as registry_mod
from agent_trace.record import (
    _claude_PostToolUse,
    _cursor_afterFileEdit,
    _ranges_from_multiedit,
    _ranges_from_write,
)


def _git_init_with_commit(repo: str) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@e.st"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    Path(repo, ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


class TestRangesFromWrite(unittest.TestCase):
    def test_multiline_content(self) -> None:
        rp, rc = _ranges_from_write("f.py", "a\nb\nc")
        self.assertIsNotNone(rp)
        assert rp is not None
        self.assertEqual(rp[0], {"start_line": 1, "end_line": 3})
        self.assertEqual(rc, ["a\nb\nc"])

    def test_empty_returns_none(self) -> None:
        rp, rc = _ranges_from_write("f.py", "")
        self.assertIsNone(rp)
        self.assertIsNone(rc)


class TestRangesFromMultiedit(unittest.TestCase):
    def test_three_edits_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.py"
            p.write_text("alpha\nbeta\ngamma\n")
            edits = [
                {"old_string": "beta", "new_string": "BETA"},
                {"old_string": "gamma", "new_string": "G\nA\n"},
            ]
            rp, rc = _ranges_from_multiedit(str(p), edits)
            self.assertIsNotNone(rp)
            assert rp is not None and rc is not None
            self.assertEqual(len(rp), 2)
            self.assertEqual(len(rc), 2)


class TestClaudePostToolUse(unittest.TestCase):
    def test_write_tool_uses_content_not_new_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            fp = str(Path(tmp) / "new.py")
            d = {
                "tool_name": "Write",
                "tool_input": {"file_path": fp, "content": "def x():\n    return 1\n"},
                "session_id": "s1",
                "cwd": tmp,
            }
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                trace, ev = _claude_PostToolUse(d)
            self.assertEqual(ev, "PostToolUse")
            self.assertIsNotNone(trace)
            assert trace is not None
            conv = trace["files"][0]["conversations"][0]
            r0 = conv["ranges"][0]
            self.assertIn("line_hashes", r0)
            self.assertGreaterEqual(r0["end_line"], 2)
            meta = trace.get("metadata") or {}
            self.assertTrue(meta.get("is_creation"))

    def test_multiedit_produces_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            p = Path(tmp) / "m.py"
            p.write_text("one\ntwo\n")
            d = {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": str(p),
                    "edits": [
                        {"old_string": "one", "new_string": "1"},
                        {"old_string": "two", "new_string": "2"},
                    ],
                },
                "session_id": "s2",
                "cwd": tmp,
            }
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                trace, ev = _claude_PostToolUse(d)
            self.assertEqual(ev, "PostToolUse")
            assert trace is not None
            conv = trace["files"][0]["conversations"][0]
            self.assertEqual(len(conv["ranges"]), 2)

    def test_notebook_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            d = {
                "tool_name": "NotebookEdit",
                "tool_input": {
                    "notebook_path": "analysis.ipynb",
                    "cell_id": "c7",
                    "new_source": "import pandas as pd\n",
                },
                "session_id": "s3",
                "cwd": tmp,
            }
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                trace, ev = _claude_PostToolUse(d)
        self.assertEqual(ev, "PostToolUse")
        assert trace is not None
        meta = trace.get("metadata") or {}
        self.assertEqual(meta.get("cell_id"), "c7")


class TestCursorAfterFileEdit(unittest.TestCase):
    def test_create_file_empty_old_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            fp = str(Path(tmp) / "created.py")
            Path(fp).write_text("import os")
            d = {
                "hook_event_name": "afterFileEdit",
                "file_path": fp,
                "edits": [{"old_string": "", "new_string": "import os"}],
                "conversation_id": "c1",
            }
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                trace, ev = _cursor_afterFileEdit(d)
            self.assertEqual(ev, "afterFileEdit")
            assert trace is not None
            conv = trace["files"][0]["conversations"][0]
            r0 = conv["ranges"][0]
            self.assertEqual(r0["start_line"], 1)
            self.assertEqual(r0["end_line"], 1)


if __name__ == "__main__":
    unittest.main()
