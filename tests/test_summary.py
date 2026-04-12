"""Tests for pluggable session summaries (Phase 6)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace.config import get_project_config, save_project_config
from agent_trace.models import Trace
from agent_trace.record import record_from_stdin
from agent_trace.storage import (
    ensure_project_dir,
    get_ledgers_path,
    get_session_summaries_path,
    get_traces_path,
    write_in_repo_pointer,
)
from agent_trace.summary import (
    append_summary,
    generate_summary,
    get_summary_for_commit,
    merge_note_summaries,
    run_summary_generate,
)


def _tmp_dir() -> str:
    """Workspace-local temp (avoids sandbox issues with system temp)."""
    root = Path(__file__).resolve().parent.parent / ".pytest_agent_trace_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(dir=root)


def _fake_repo_with_project(pid: str, parent: Path) -> Path:
    """A directory with only ``.agent-trace/project.json`` (no git required for resolution)."""
    base = parent / "repo"
    base.mkdir(parents=True)
    write_in_repo_pointer(str(base), pid)
    return base


def _minimal_trace_dict(tid: str, session_id: str) -> dict:
    return {
        "version": "2.0",
        "id": tid,
        "timestamp": "2026-04-01T12:00:00+00:00",
        "tool": {"name": "cursor"},
        "files": [{"path": "f.py", "conversations": []}],
        "metadata": {"session_id": session_id, "conversation_id": session_id},
    }


class TestGenerateSummary(unittest.TestCase):
    def test_echo_json(self) -> None:
        tr = Trace.from_dict(_minimal_trace_dict("t1", "s1"))
        py = (
            "import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'f.py': 'summary text'}))"
        )
        cmd = f"{sys.executable} -c {repr(py)}"
        out = generate_summary([tr], cmd, timeout_seconds=30)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("f.py"), "summary text")

    def test_timeout_kills(self) -> None:
        tr = Trace.from_dict(_minimal_trace_dict("t1", "s1"))
        cmd = f"{sys.executable} -c \"import time; time.sleep(60)\""
        out = generate_summary([tr], cmd, timeout_seconds=1)
        self.assertIsNone(out)


class TestSessionSummariesStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "proj-sum-test"
        ensure_project_dir(self.pid)

    def tearDown(self) -> None:
        self._p.stop()

    def test_append_and_latest_for_commit(self) -> None:
        append_summary(self.pid, "sess-a", {"a.py": "one"})
        append_summary(self.pid, "sess-a", {"a.py": "two"})
        lp = get_ledgers_path(self.pid)
        commit_sha = "c" * 40
        ledger = {
            "version": "1.0",
            "commit_sha": commit_sha,
            "parent_sha": "p" * 40,
            "committed_at": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "trace_ids": ["tid1"],
            "files": {},
        }
        lp.write_text(json.dumps(ledger) + "\n")
        tp = get_traces_path(self.pid)
        tp.write_text(json.dumps(_minimal_trace_dict("tid1", "sess-a")) + "\n")
        merged = get_summary_for_commit(self.pid, commit_sha)
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertEqual(merged.get("a.py"), "two")

    def test_failed_command_does_not_append(self) -> None:
        path = get_session_summaries_path(self.pid)
        path.write_text("")
        before = path.read_text()
        tr = Trace.from_dict(_minimal_trace_dict("t1", "s1"))
        out = generate_summary([tr], f"{sys.executable} -c \"import sys; sys.exit(1)\"")
        self.assertIsNone(out)
        self.assertEqual(path.read_text(), before)


class TestMergeNoteSummaries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "merge-pid"
        self.repo = _fake_repo_with_project(self.pid, Path(self.tmp))
        ensure_project_dir(self.pid)

    def tearDown(self) -> None:
        self._p.stop()

    def test_static_plus_session(self) -> None:
        sha = "a" * 40
        append_summary(self.pid, "s1", {"dyn.py": "from session"})
        get_ledgers_path(self.pid).write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "commit_sha": sha,
                    "parent_sha": None,
                    "committed_at": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "trace_ids": ["z1"],
                    "files": {},
                },
            )
            + "\n",
        )
        get_traces_path(self.pid).write_text(json.dumps(_minimal_trace_dict("z1", "s1")) + "\n")
        led = {"commit_sha": sha, "trace_ids": ["z1"]}
        m = merge_note_summaries(str(self.repo), led, {"static.txt": "static"})
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m["static.txt"], "static")
        self.assertEqual(m["dyn.py"], "from session")


class TestSummaryHookIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "hook-pid"
        self.repo = _fake_repo_with_project(self.pid, Path(self.tmp))
        ensure_project_dir(self.pid)
        py = (
            "import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'hooked.py': 'from hook'}))"
        )
        save_project_config(
            {
                "storage": "local",
                "summary": {
                    "enabled": True,
                    "command": f"{sys.executable} -c {repr(py)}",
                    "timeout_seconds": 30,
                },
            },
            str(self.repo),
        )
        get_traces_path(self.pid).write_text(
            json.dumps(_minimal_trace_dict("ht1", "conv-99")) + "\n",
        )

    def tearDown(self) -> None:
        self._p.stop()

    def test_after_agent_response_writes_jsonl(self) -> None:
        payload = json.dumps(
            {
                "hook_event_name": "afterAgentResponse",
                "conversation_id": "conv-99",
                "cwd": str(self.repo),
            },
        )
        import io

        with patch.object(sys, "stdin", io.StringIO(payload)):
            record_from_stdin()
        path = get_session_summaries_path(self.pid)
        self.assertTrue(path.is_file())
        line = path.read_text().strip()
        row = json.loads(line)
        self.assertEqual(row["session_id"], "conv-99")
        self.assertEqual(row["summaries"]["hooked.py"], "from hook")


class TestSummaryCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "cli-pid"
        self.repo = _fake_repo_with_project(self.pid, Path(self.tmp))
        ensure_project_dir(self.pid)
        save_project_config({"storage": "local"}, str(self.repo))

    def tearDown(self) -> None:
        self._p.stop()

    def test_enable_cat(self) -> None:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_trace.cli",
                "summary",
                "enable",
                "--command",
                "cat",
            ],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "AGENT_TRACE_HOME": self.tmp,
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            },
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        cfg = get_project_config(str(self.repo))
        self.assertIsNotNone(cfg)
        assert cfg is not None
        sm = cfg.get("summary") or {}
        self.assertTrue(sm.get("enabled"))
        self.assertEqual(sm.get("command"), "cat")


class TestRunSummaryGenerate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "gen-pid"
        self.repo = _fake_repo_with_project(self.pid, Path(self.tmp))
        ensure_project_dir(self.pid)
        py = (
            "import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'g.py': 'generated'}))"
        )
        save_project_config(
            {
                "storage": "local",
                "summary": {
                    "enabled": True,
                    "command": f"{sys.executable} -c {repr(py)}",
                },
            },
            str(self.repo),
        )
        get_traces_path(self.pid).write_text(
            json.dumps(_minimal_trace_dict("g1", "sess-gen")) + "\n",
        )

    def tearDown(self) -> None:
        self._p.stop()

    def test_manual_generate(self) -> None:
        out = run_summary_generate(str(self.repo), "sess-gen")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["g.py"], "generated")
        path = get_session_summaries_path(self.pid)
        self.assertIn("generated", path.read_text())
