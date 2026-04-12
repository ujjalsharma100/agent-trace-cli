"""Tests for attribution ledger (Phase 1a rename handling)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_trace.ledger import (
    _build_trace_hash_index,
    _get_rename_map,
    _trace_file_matches,
)


class TestTraceFileMatches(unittest.TestCase):
    def test_alternate_path(self) -> None:
        self.assertTrue(_trace_file_matches("src/old.py", "src/new.py", ["src/old.py"]))
        self.assertFalse(_trace_file_matches("other.py", "src/new.py", ["src/old.py"]))


class TestGetRenameMap(unittest.TestCase):
    def test_detects_rename_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            subprocess.run(["git", "init"], cwd=tmp, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=tmp, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
            (p / "a.txt").write_text("hello\n")
            subprocess.run(["git", "add", "a.txt"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, check=True)
            subprocess.run(["git", "mv", "a.txt", "b.txt"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-m", "rename"], cwd=tmp, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmp, capture_output=True, text=True, check=True,
            ).stdout.strip()
            parent = subprocess.run(
                ["git", "rev-parse", "HEAD^"],
                cwd=tmp, capture_output=True, text=True, check=True,
            ).stdout.strip()
            m = _get_rename_map(parent, head, tmp)
            self.assertEqual(m.get("b.txt"), "a.txt")


class TestBuildTraceHashIndexRenames(unittest.TestCase):
    def test_includes_hashes_from_old_path(self) -> None:
        trace = {
            "id": "t1",
            "metadata": {"edit_sequence": 0},
            "tool": {"name": "cursor"},
            "files": [{
                "path": "legacy/name.py",
                "conversations": [{
                    "contributor": {"model_id": "m"},
                    "ranges": [{
                        "line_hashes": [{"hash": "sha256:abc123"}],
                    }],
                }],
            }],
        }
        idx = _build_trace_hash_index([trace], "new/name.py", alternate_paths=["legacy/name.py"])
        self.assertIn("sha256:abc123", idx)


if __name__ == "__main__":
    unittest.main()
