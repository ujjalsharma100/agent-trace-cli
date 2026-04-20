"""Round-trip and JSON Schema validation for :mod:`agent_trace.models`."""

from __future__ import annotations

import json
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[misc, assignment]

from agent_trace.models import (
    CommitLink,
    GitNote,
    Ledger,
    RemotesConfig,
    SyncState,
    Trace,
    schemas_dir,
)
from agent_trace.trace import ProjectResolution, create_trace


def _schema(name: str) -> dict:
    return json.loads((schemas_dir() / name).read_text())


def _validate(instance: dict, schema_name: str) -> None:
    if Draft202012Validator is None:
        raise unittest.SkipTest("jsonschema is not installed")
    schema = _schema(schema_name)
    Draft202012Validator(schema).validate(instance)


def _fake_resolution(rel_path: str = "src/example.py") -> ProjectResolution:
    return ProjectResolution(
        repo_root="/tmp/agent-trace-test-repo",
        project_id="at_test00000001",
        rel_path=rel_path,
        vcs={"type": "git", "revision": "deadbeef" * 5},
    )


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class TestTraceSchema(unittest.TestCase):
    def test_trace_roundtrip_from_create_trace(self) -> None:
        tdict = create_trace(
            "ai",
            "src/example.py",
            model="claude-3-5-sonnet-20241022",
            range_positions=[{"start_line": 1, "end_line": 2}],
            range_contents=["line1\nline2"],
            metadata={"session_id": "s1"},
            edit_sequence=3,
            resolution=_fake_resolution(),
        )
        assert tdict is not None
        tdict["version"] = "2.0"
        t = Trace.from_dict(tdict)
        back = Trace.from_dict(t.to_dict())
        self.assertEqual(t, back)

    def test_trace_validates_schema(self) -> None:
        tdict = create_trace(
            "ai",
            "README.md",
            range_positions=[{"start_line": 1, "end_line": 1}],
            range_contents=["hello"],
            resolution=_fake_resolution("README.md"),
        )
        assert tdict is not None
        _validate(tdict, "trace-record.schema.json")


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class TestLedgerAndCommitLink(unittest.TestCase):
    def test_ledger_roundtrip(self) -> None:
        raw = {
            "version": "2.0",
            "commit_sha": "abc123",
            "parent_sha": "def456",
            "parent_committed_at": "2026-04-12T11:59:00+00:00",
            "committed_at": "2026-04-12T12:00:00+00:00",
            "created_at": "2026-04-12T12:00:01+00:00",
            "trace_ids": ["tid-1"],
            "files": {
                "a.py": {
                    "line_attributions": [
                        {
                            "start_line": 1,
                            "end_line": 5,
                            "type": "ai",
                            "trace_id": "tid-1",
                            "model_id": "anthropic/claude-3-5-sonnet-20241022",
                        }
                    ]
                }
            },
        }
        leg = Ledger.from_dict(raw)
        self.assertEqual(Ledger.from_dict(leg.to_dict()), leg)
        _validate(leg.to_dict(), "ledger.schema.json")

    def test_commit_link_roundtrip(self) -> None:
        raw = {
            "commit_sha": "abc",
            "parent_sha": "def",
            "trace_ids": ["t1"],
            "files_changed": ["x.py"],
            "committed_at": "2026-04-12T12:00:00+00:00",
            "created_at": "2026-04-12T12:00:01+00:00",
        }
        cl = CommitLink.from_dict(raw)
        self.assertEqual(CommitLink.from_dict(cl.to_dict()), cl)
        _validate(cl.to_dict(), "commit-link.schema.json")


@unittest.skipIf(Draft202012Validator is None, "jsonschema is not installed")
class TestGitNoteRemotesSync(unittest.TestCase):
    def test_git_note_roundtrip(self) -> None:
        raw = {
            "version": "2.0",
            "trace_ids": ["u1"],
            "ledger_hash": "sha256:" + "ab" * 32,
            "stats": {"ai_lines": 1},
            "prompts": ["fix auth"],
        }
        gn = GitNote.from_dict(raw)
        self.assertEqual(GitNote.from_dict(gn.to_dict()), gn)
        _validate(gn.to_dict(), "git-note.schema.json")

    def test_remotes_roundtrip(self) -> None:
        raw = {
            "origin": {
                "url": "https://traces.example.com",
                "auth": {"type": "bearer", "token_ref": "global:origin"},
            }
        }
        rc = RemotesConfig.from_dict(raw)
        self.assertEqual(RemotesConfig.from_dict(rc.to_dict()), rc)
        _validate(rc.to_dict(), "remotes.schema.json")

    def test_sync_state_roundtrip(self) -> None:
        raw = {
            "remotes": {
                "origin": {
                    "last_push": {
                        "traces_max_timestamp": "2026-04-12T09:32:00+00:00",
                        "ledgers_max_commit_at": "2026-04-12T09:32:00+00:00",
                    },
                    "last_pull": {"at": "2026-04-11T18:00:00+00:00"},
                }
            }
        }
        ss = SyncState.from_dict(raw)
        self.assertEqual(SyncState.from_dict(ss.to_dict()), ss)
        _validate(ss.to_dict(), "sync-state.schema.json")


if __name__ == "__main__":
    unittest.main()
