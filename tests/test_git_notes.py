"""Tests for git notes (refs/notes/agent-trace)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_trace import blame, git_notes
from agent_trace.models import Trace, schemas_dir

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[misc, assignment]

_HAVE_SCHEMA = Draft202012Validator is not None


def _validate(instance: dict, schema_name: str) -> None:
    if Draft202012Validator is None:
        raise unittest.SkipTest("jsonschema is not installed")
    schema = json.loads((schemas_dir() / schema_name).read_text())
    Draft202012Validator(schema).validate(instance)


def _git_init_with_commit(repo: Path) -> str:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("a\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "m"], cwd=repo, check=True, capture_output=True)
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


@unittest.skipUnless(_HAVE_SCHEMA, "jsonschema not installed")
class TestBuildNote(unittest.TestCase):
    def test_schema_valid(self) -> None:
        leg = {
            "version": "2.0",
            "commit_sha": "a" * 40,
            "parent_sha": None,
            "committed_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "trace_ids": ["tid1"],
            "files": {
                "x.py": {
                    "line_attributions": [
                        {
                            "start_line": 1,
                            "end_line": 2,
                            "type": "ai",
                            "trace_id": "tid1",
                            "model_id": "m",
                        },
                    ],
                },
            },
        }
        tr = Trace.from_dict(
            {
                "version": "2.0",
                "id": "tid1",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "tool": {"name": "c"},
                "files": [],
                "metadata": {"prompt": "hello world"},
            },
        )
        note = git_notes.build_note(
            leg,
            [tr],
            include_ledger=True,
            include_summary=False,
            include_prompts=True,
        )
        _validate(note, "git-note.schema.json")
        self.assertEqual(note["version"], "2.0")
        self.assertIn("ledger", note)
        self.assertIn("prompts", note)

    def test_ledger_hash_stable(self) -> None:
        leg = {"a": 1, "b": [3, 2]}
        h1 = git_notes.ledger_dict_hash(leg)
        h2 = git_notes.ledger_dict_hash(leg)
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("sha256:"))
        canon = json.dumps(leg, sort_keys=True, separators=(",", ":"))
        exp = "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
        self.assertEqual(h1, exp)


class TestGitNotesRepo(unittest.TestCase):
    def test_attach_read_strip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = _git_init_with_commit(repo)
            leg = {
                "version": "2.0",
                "commit_sha": sha,
                "parent_sha": None,
                "committed_at": None,
                "created_at": "2026-01-01T00:00:00+00:00",
                "trace_ids": ["tid1"],
                "files": {
                    "f.txt": {
                        "line_attributions": [
                            {
                                "start_line": 1,
                                "end_line": 1,
                                "type": "ai",
                                "trace_id": "tid1",
                            },
                        ],
                    },
                },
            }
            note = git_notes.build_note(
                leg,
                [],
                include_ledger=True,
                include_summary=False,
                include_prompts=False,
            )
            self.assertTrue(git_notes.attach_note(sha, note, str(repo)))
            got = git_notes.read_note(sha, str(repo))
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got.get("ledger_hash"), note["ledger_hash"])
            self.assertTrue(git_notes.strip_sections(sha, ["ledger"], str(repo)))
            got2 = git_notes.read_note(sha, str(repo))
            self.assertIsNotNone(got2)
            assert got2 is not None
            self.assertNotIn("ledger", got2)


class TestBlameNoteFallback(unittest.TestCase):
    def test_merge_ledgers_from_git_notes(self) -> None:
        sha = "c" * 40
        segments = [
            {
                "commit_sha": sha,
                "start_line": 1,
                "end_line": 1,
                "orig_start_line": 1,
                "orig_end_line": 1,
                "content_lines": ["x"],
            },
        ]
        note = {
            "version": "2.0",
            "trace_ids": [],
            "ledger_hash": "sha256:" + "ab" * 32,
            "stats": {"ai_lines": 1},
            "ledger": {
                "files": {
                    "f.py": {
                        "line_attributions": [
                            {
                                "start_line": 1,
                                "end_line": 1,
                                "type": "ai",
                                "trace_id": "t1",
                                "model_id": "m",
                            },
                        ],
                    },
                },
            },
        }
        with mock.patch.object(blame, "read_note", return_value=note):
            merged = blame._merge_ledgers_from_git_notes("/repo", segments, {})
        self.assertIn(sha, merged)
        self.assertIn("files", merged[sha])


class TestConfigureRefspecs(unittest.TestCase):
    def test_adds_refspecs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", "https://example.com/x.git"], cwd=repo, check=True)
            from agent_trace.hooks import configure_git_notes_refspecs

            self.assertTrue(configure_git_notes_refspecs(str(repo)))
            r = subprocess.run(
                ["git", "config", "--get-all", "remote.origin.fetch"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertIn("refs/notes/agent-trace", r.stdout)


if __name__ == "__main__":
    unittest.main()
