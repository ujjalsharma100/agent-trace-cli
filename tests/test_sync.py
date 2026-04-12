"""Tests for push/pull sync protocol (Phase 4)."""

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
    compute_attributed_trace_ids,
    push,
    pull,
    status,
    _load_sync_state,
    _save_sync_state,
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
        self.assertEqual(state, {"remotes": {}})

    def test_save_load(self):
        state = {"remotes": {"origin": {"last_push": {"traces_max_timestamp": "2026-01-01T00:00:00Z"}}}}
        _save_sync_state(self.pid, state)
        loaded = _load_sync_state(self.pid)
        self.assertEqual(loaded, state)


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


class TestPushDryRun(unittest.TestCase):
    """Verify push --dry-run counts correctly without making HTTP calls."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com", token="t")

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
