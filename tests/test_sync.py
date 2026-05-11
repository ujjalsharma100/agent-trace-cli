"""Tests for push/pull sync protocol — content-ID manifest model."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent_trace.storage import ensure_project_dir, get_traces_path, get_ledgers_path, get_commit_links_path
from agent_trace.sync import (
    PushResult,
    PullResult,
    StatusReport,
    _read_traces,
    _read_ledgers,
    _read_commit_links,
    _append_jsonl_dedupe,
    _empty_remote_state,
    _get_remote_state,
    _load_sync_state,
    _save_sync_state,
    _synced_set,
    compute_attributed_trace_ids,
    push,
    pull,
    status,
)
from agent_trace.remote import add_remote


class TestSyncStateRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_load_empty(self):
        state = _load_sync_state(self.pid)
        self.assertEqual(state, {"version": 2, "remotes": {}})

    def test_save_load(self):
        rs = _empty_remote_state()
        rs["synced"]["trace_ids"] = ["t1", "t2"]
        rs["cursor"]["traces"] = "2026-01-01T00:00:00Z"
        state = {"version": 2, "remotes": {"origin": rs}}
        _save_sync_state(self.pid, state)
        loaded = _load_sync_state(self.pid)
        self.assertEqual(loaded["remotes"]["origin"]["synced"]["trace_ids"], ["t1", "t2"])
        self.assertEqual(loaded["remotes"]["origin"]["cursor"]["traces"], "2026-01-01T00:00:00Z")

    def test_legacy_v1_state_is_discarded(self):
        """Legacy last_push/last_pull cursors are silently dropped on load —
        the manifest rebuilds itself on the next sync."""
        legacy = {
            "remotes": {
                "origin": {
                    "last_push": {"traces_max_timestamp": "2026-01-01T00:00:00Z"},
                    "last_pull": {"at": "2026-01-02T00:00:00Z"},
                }
            }
        }
        _save_sync_state(self.pid, legacy)
        loaded = _load_sync_state(self.pid)
        self.assertIn("origin", loaded["remotes"])
        self.assertEqual(loaded["remotes"]["origin"], _empty_remote_state())


class TestLocalReaders(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_read_empty(self):
        self.assertEqual(_read_traces(self.pid), [])
        self.assertEqual(_read_ledgers(self.pid), [])
        self.assertEqual(_read_commit_links(self.pid), [])

    def test_read_traces(self):
        tp = get_traces_path(self.pid)
        tp.write_text(json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n")
        traces = _read_traces(self.pid)
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["id"], "t1")


class TestAttributedTraceIds(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_no_ledgers(self):
        self.assertEqual(compute_attributed_trace_ids(self.pid), set())

    def test_with_ledgers(self):
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1", "t2"], "created_at": "2026-01-01"}) + "\n"
            + json.dumps({"commit_sha": "def", "trace_ids": ["t2", "t3"], "created_at": "2026-01-02"}) + "\n"
        )
        ids = compute_attributed_trace_ids(self.pid)
        self.assertEqual(ids, {"t1", "t2", "t3"})


class TestAppendJsonlDedupe(unittest.TestCase):
    def test_deduplicates(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"id": "existing"}) + "\n")
            path = Path(f.name)
        try:
            added = _append_jsonl_dedupe(path, [
                {"id": "existing"},
                {"id": "new1"},
                {"id": "new2"},
            ], key="id")
            self.assertEqual(added, 2)
            lines = path.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
        finally:
            path.unlink()

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = Path(f.name)
        try:
            added = _append_jsonl_dedupe(path, [{"id": "a"}, {"id": "b"}], key="id")
            self.assertEqual(added, 2)
        finally:
            path.unlink()


class TestStatusReport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_status_no_remote(self):
        report = status(self.pid)
        self.assertEqual(report.project_id, self.pid)
        self.assertIsNone(report.remote_name)
        self.assertEqual(report.total_traces, 0)

    def test_status_with_data(self):
        tp = get_traces_path(self.pid)
        tp.write_text(
            json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n"
            + json.dumps({"id": "t2", "timestamp": "2026-01-02"}) + "\n"
        )
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1"], "created_at": "2026-01-01"}) + "\n"
        )
        report = status(self.pid)
        self.assertEqual(report.total_traces, 2)
        self.assertEqual(report.total_ledgers, 1)
        self.assertEqual(report.unattributed_traces, 1)

    def test_pulled_traces_are_not_reported_as_unpushed(self):
        """Regression: with timestamp cursors, traces pulled from remote
        showed as ``unpushed`` because their own timestamp could be newer
        than the local push high-water-mark. Manifest model fixes this."""
        add_remote(self.pid, "origin", "https://traces.example.com/acme/myrepo", token="t")

        tp = get_traces_path(self.pid)
        tp.write_text(
            json.dumps({"id": "t1", "timestamp": "2026-05-09"}) + "\n"
            + json.dumps({"id": "t2", "timestamp": "2026-05-10"}) + "\n"
        )
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1", "t2"], "created_at": "2026-05-09"}) + "\n"
        )

        # Simulate that both traces and the ledger were pulled from origin —
        # they should already be in the synced manifest.
        state = _load_sync_state(self.pid)
        rs = _get_remote_state(state, "origin")
        rs["synced"]["trace_ids"] = ["t1", "t2"]
        rs["synced"]["ledger_shas"] = ["abc"]
        _save_sync_state(self.pid, state)

        report = status(self.pid, remote_name="origin")
        self.assertEqual(report.unpushed_traces, 0)
        self.assertEqual(report.unpushed_ledgers, 0)


class TestPushDryRun(unittest.TestCase):
    """Verify push --dry-run counts correctly without making HTTP calls."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com/acme/myrepo", token="t")

    def tearDown(self):
        self._env_patch.stop()

    def test_push_dry_run_attributed_only(self):
        tp = get_traces_path(self.pid)
        tp.write_text(
            json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n"
            + json.dumps({"id": "t2", "timestamp": "2026-01-02"}) + "\n"
        )
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1"], "created_at": "2026-01-01"}) + "\n"
        )
        result = push(self.pid, dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.traces_pushed, 1)
        self.assertEqual(result.traces_held_back, 1)
        self.assertEqual(result.ledgers_pushed, 1)

    def test_push_dry_run_full(self):
        tp = get_traces_path(self.pid)
        tp.write_text(
            json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n"
            + json.dumps({"id": "t2", "timestamp": "2026-01-02"}) + "\n"
        )
        result = push(self.pid, full=True, dry_run=True)
        self.assertEqual(result.traces_pushed, 2)
        self.assertEqual(result.traces_held_back, 0)

    def test_push_skips_already_synced(self):
        """Items already in the manifest are not re-pushed — even in dry-run."""
        tp = get_traces_path(self.pid)
        tp.write_text(
            json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n"
            + json.dumps({"id": "t2", "timestamp": "2026-01-02"}) + "\n"
        )
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1", "t2"], "created_at": "2026-01-01"}) + "\n"
        )
        # Mark t1 as already synced.
        state = _load_sync_state(self.pid)
        rs = _get_remote_state(state, "origin")
        rs["synced"]["trace_ids"] = ["t1"]
        _save_sync_state(self.pid, state)

        result = push(self.pid, dry_run=True)
        self.assertEqual(result.traces_pushed, 1)  # only t2


class TestPushRecordsManifest(unittest.TestCase):
    """Successful pushes add IDs to the synced manifest so the next push is a no-op."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com/acme/myrepo", token="t")

    def tearDown(self):
        self._env_patch.stop()

    def test_push_then_repeat_pushes_nothing(self):
        tp = get_traces_path(self.pid)
        tp.write_text(json.dumps({"id": "t1", "timestamp": "2026-01-01"}) + "\n")
        lp = get_ledgers_path(self.pid)
        lp.write_text(
            json.dumps({"commit_sha": "abc", "trace_ids": ["t1"], "created_at": "2026-01-01"}) + "\n"
        )

        with patch("agent_trace.sync._http_post") as mock_post:
            mock_post.return_value = {"ok": True, "count": 1}
            r1 = push(self.pid)
            self.assertEqual(r1.traces_pushed, 1)
            self.assertEqual(r1.ledgers_pushed, 1)

            r2 = push(self.pid)
            self.assertEqual(r2.traces_pushed, 0)
            self.assertEqual(r2.ledgers_pushed, 0)

        state = _load_sync_state(self.pid)
        rs = state["remotes"]["origin"]
        self.assertIn("t1", rs["synced"]["trace_ids"])
        self.assertIn("abc", rs["synced"]["ledger_shas"])


class TestPullPaginatesAndManifests(unittest.TestCase):
    """A single pull walks pages until short, dedupes by ID, and records IDs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com/acme/myrepo", token="t")

    def tearDown(self):
        self._env_patch.stop()

    def test_pull_paginates_traces(self):
        # Build two pages: 500 traces, then 1 trace.
        page_size = 500
        page1 = [{"id": f"t{i}", "timestamp": "2026-01-01"} for i in range(page_size)]
        page2 = [{"id": "t-tail", "timestamp": "2026-01-02"}]

        responses = [
            {"items": page1, "max_timestamp": "2026-04-30T00:00:00+00:00"},
            {"items": page2, "max_timestamp": "2026-05-01T00:00:00+00:00"},
            # ledgers / commit-links / conversations all empty.
            {"items": [], "max_timestamp": None},
            {"items": [], "max_timestamp": None},
            {"items": [], "max_timestamp": None},
        ]

        with patch("agent_trace.sync._http_get") as mock_get:
            mock_get.side_effect = responses
            result = pull(self.pid)

        self.assertEqual(result.traces_pulled, page_size + 1)

        state = _load_sync_state(self.pid)
        rs = state["remotes"]["origin"]
        self.assertIn("t-tail", rs["synced"]["trace_ids"])
        self.assertIn("t0", rs["synced"]["trace_ids"])
        self.assertEqual(rs["cursor"]["traces"], "2026-05-01T00:00:00+00:00")


class TestHttpStrippedFromHooks(unittest.TestCase):
    """Verify that record.py and commit_link.py contain no HTTP calls."""

    def test_record_no_urllib(self):
        import agent_trace.record as rec_mod
        import inspect
        src = inspect.getsource(rec_mod)
        self.assertNotIn("urllib.request.urlopen", src)
        self.assertNotIn("urllib.request.Request", src)

    def test_commit_link_no_urllib(self):
        import agent_trace.commit_link as cl_mod
        import inspect
        src = inspect.getsource(cl_mod)
        self.assertNotIn("urllib.request.urlopen", src)
        self.assertNotIn("urllib.request.Request", src)


if __name__ == "__main__":
    unittest.main()
