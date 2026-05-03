"""Tests for URL-keyed transcript summaries."""

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
from agent_trace.record import record_from_stdin
from agent_trace.session import touch_session_project
from agent_trace.storage import (
    ensure_project_dir,
    get_ledgers_path,
    get_session_summaries_path,
    get_traces_path,
)
from agent_trace.summary import (
    append_summary,
    generate_summary_text,
    get_summary_for_commit,
    latest_summary_by_url,
    merge_note_summaries,
    run_summary_generate,
)
from agent_trace.summary_presets import build_preset_command


def _tmp_dir() -> str:
    """Workspace-local temp (avoids sandbox issues with system temp)."""
    root = Path(__file__).resolve().parent.parent / ".pytest_agent_trace_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(dir=root)


def _fake_repo_with_project(parent: Path) -> tuple[Path, str]:
    """A git-initialized directory whose project_id is derived from its path."""
    from agent_trace.storage import path_to_project_id

    base = parent / "repo"
    base.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(base), check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(base), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(base), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (base / "f.txt").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(base), "add", "f.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(base), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    pid = path_to_project_id(str(base))
    return base, pid


def _trace_with_url(tid: str, session_id: str, url: str) -> dict:
    return {
        "version": "2.0",
        "id": tid,
        "timestamp": "2026-04-01T12:00:00+00:00",
        "tool": {"name": "claude-code"},
        "files": [
            {
                "path": "f.py",
                "conversations": [
                    {
                        "contributor": {"type": "ai", "model_id": "claude"},
                        "ranges": [{"start_line": 1, "end_line": 1}],
                        "url": url,
                    }
                ],
            }
        ],
        "metadata": {"session_id": session_id, "conversation_id": session_id},
    }


def _ledger_with_url(commit_sha: str, url: str, trace_id: str = "tid1") -> dict:
    return {
        "version": "1.0",
        "commit_sha": commit_sha,
        "parent_sha": "p" * 40,
        "committed_at": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "trace_ids": [trace_id],
        "files": {
            "f.py": {
                "line_attributions": [
                    {
                        "start_line": 1,
                        "end_line": 1,
                        "type": "ai",
                        "trace_id": trace_id,
                        "conversation_url": url,
                    }
                ]
            }
        },
    }


class TestGenerateSummaryText(unittest.TestCase):
    def test_echo_text(self) -> None:
        out = generate_summary_text("hello world", "cat", timeout_seconds=10)
        self.assertEqual(out, "hello world")

    def test_empty_text(self) -> None:
        self.assertIsNone(generate_summary_text("", "cat", timeout_seconds=10))

    def test_timeout_kills(self) -> None:
        cmd = f"{sys.executable} -c \"import time; time.sleep(60)\""
        self.assertIsNone(generate_summary_text("x", cmd, timeout_seconds=1))

    def test_nonzero_exit(self) -> None:
        cmd = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        self.assertIsNone(generate_summary_text("x", cmd, timeout_seconds=10))

    def test_blank_stdout(self) -> None:
        cmd = f"{sys.executable} -c \"pass\""
        self.assertIsNone(generate_summary_text("x", cmd, timeout_seconds=10))


class TestAppendAndLookup(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.pid = "proj-sum-test"
        ensure_project_dir(self.pid)

    def tearDown(self) -> None:
        self._p.stop()

    def test_latest_wins(self) -> None:
        url = "file:///tmp/transcript.jsonl"
        append_summary(self.pid, url, "first")
        append_summary(self.pid, url, "second")
        m = latest_summary_by_url(self.pid)
        self.assertEqual(m[url], "second")

    def test_summary_for_commit_walks_ledger_urls(self) -> None:
        url = "file:///tmp/transcript-x.jsonl"
        append_summary(self.pid, url, "the summary")
        commit_sha = "c" * 40
        get_ledgers_path(self.pid).write_text(
            json.dumps(_ledger_with_url(commit_sha, url)) + "\n",
        )
        out = get_summary_for_commit(self.pid, commit_sha)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out, {url: "the summary"})

    def test_failed_command_does_not_append(self) -> None:
        path = get_session_summaries_path(self.pid)
        path.write_text("")
        before = path.read_text()
        out = generate_summary_text("x", f"{sys.executable} -c \"import sys; sys.exit(1)\"")
        self.assertIsNone(out)
        self.assertEqual(path.read_text(), before)


class TestMergeNoteSummaries(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.repo, self.pid = _fake_repo_with_project(Path(self.tmp))
        ensure_project_dir(self.pid)

    def tearDown(self) -> None:
        self._p.stop()

    def test_url_keyed(self) -> None:
        sha = "a" * 40
        url = "file:///tmp/transcript-merge.jsonl"
        append_summary(self.pid, url, "from session")
        get_ledgers_path(self.pid).write_text(
            json.dumps(_ledger_with_url(sha, url, trace_id="z1")) + "\n",
        )
        # Static map argument is accepted for caller compat but ignored.
        m = merge_note_summaries(str(self.repo), {"commit_sha": sha}, {"static.txt": "x"})
        self.assertEqual(m, {url: "from session"})


class TestSummaryHookIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.repo, self.pid = _fake_repo_with_project(Path(self.tmp))
        ensure_project_dir(self.pid)
        save_project_config(
            {
                "summary": {
                    "enabled": True,
                    "command": "cat",
                    "timeout_seconds": 30,
                },
            },
            str(self.repo),
        )
        self.transcript = Path(self.tmp) / "transcript.jsonl"
        self.transcript.write_text("user: hi\nassistant: hello\n")

    def tearDown(self) -> None:
        self._p.stop()

    def test_stop_hook_reads_transcript_and_writes_jsonl(self) -> None:
        payload = json.dumps(
            {
                "hook_event_name": "Stop",
                "session_id": "conv-99",
                "cwd": str(self.repo),
                "transcript_path": str(self.transcript),
            },
        )
        import io

        with patch.object(sys, "stdin", io.StringIO(payload)):
            record_from_stdin()
        path = get_session_summaries_path(self.pid)
        self.assertTrue(path.is_file())
        line = path.read_text().strip()
        row = json.loads(line)
        self.assertEqual(row["conversation_url"], f"file://{self.transcript}")
        self.assertIn("hello", row["summary"])
        self.assertEqual(row["session_id"], "conv-99")

    def test_session_end_hook_reads_transcript_and_writes_jsonl(self) -> None:
        payload = json.dumps(
            {
                "hook_event_name": "sessionEnd",
                "session_id": "conv-session-end",
                "cwd": str(self.repo),
                "transcript_path": str(self.transcript),
            },
        )
        import io

        with patch.object(sys, "stdin", io.StringIO(payload)):
            record_from_stdin()
        path = get_session_summaries_path(self.pid)
        self.assertTrue(path.is_file())
        line = path.read_text().strip()
        row = json.loads(line)
        self.assertEqual(row["conversation_url"], f"file://{self.transcript}")
        self.assertIn("hello", row["summary"])
        self.assertEqual(row["session_id"], "conv-session-end")

    def test_session_end_uses_cursor_transcript_env(self) -> None:
        """Cursor sessionEnd JSON has no transcript_path; use CURSOR_TRANSCRIPT_PATH."""
        payload = json.dumps(
            {
                "hook_event_name": "sessionEnd",
                "session_id": "conv-environ",
                "reason": "completed",
            },
        )
        import io

        with patch.dict(
            os.environ,
            {
                "CURSOR_TRANSCRIPT_PATH": str(self.transcript),
                "CURSOR_PROJECT_DIR": str(self.repo),
            },
        ):
            with patch.object(sys, "stdin", io.StringIO(payload)):
                record_from_stdin()
        path = get_session_summaries_path(self.pid)
        self.assertTrue(path.is_file())
        line = path.read_text().strip()
        row = json.loads(line)
        self.assertEqual(row["conversation_url"], f"file://{self.transcript}")
        self.assertIn("hello", row["summary"])

    def test_session_end_uses_session_manifest_when_cwd_is_parent_of_nested_repo(
        self,
    ) -> None:
        """Summary attributes to the inner repo (where file traces and config live) when
        ``cwd`` is the outer folder — same scenario as a multi-root / parent workspace.
        """
        outer = Path(self.tmp) / "outer"
        inner = outer / "inner"
        outer.mkdir(parents=True)
        inner.mkdir()
        subprocess.run(["git", "init"], cwd=str(outer), check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(outer), "config", "user.email", "t@t"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(outer), "config", "user.name", "t"],
            check=True,
            capture_output=True,
        )
        (outer / "outer.txt").write_text("o\n")
        subprocess.run(
            ["git", "-C", str(outer), "add", "outer.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(outer), "commit", "-m", "o"],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "init"], cwd=str(inner), check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(inner), "config", "user.email", "t@t"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(inner), "config", "user.name", "t"],
            check=True,
            capture_output=True,
        )
        (inner / "f.txt").write_text("i\n")
        subprocess.run(
            ["git", "-C", str(inner), "add", "f.txt"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(inner), "commit", "-m", "i"],
            check=True,
            capture_output=True,
        )
        from agent_trace.storage import path_to_project_id

        inner_pid = path_to_project_id(str(inner))
        ensure_project_dir(inner_pid)
        save_project_config(
            {
                "summary": {
                    "enabled": True,
                    "command": "cat",
                    "timeout_seconds": 30,
                },
            },
            str(inner),
        )
        touch_session_project("conv-nested", inner_pid, transcript_path=str(self.transcript))

        payload = json.dumps(
            {
                "hook_event_name": "sessionEnd",
                "session_id": "conv-nested",
                "cwd": str(outer),
                "transcript_path": str(self.transcript),
            },
        )
        import io

        with patch.object(sys, "stdin", io.StringIO(payload)):
            record_from_stdin()
        path = get_session_summaries_path(inner_pid)
        self.assertTrue(path.is_file(), msg="summary should land in inner repo project")
        line = path.read_text().strip()
        row = json.loads(line)
        self.assertEqual(row["conversation_url"], f"file://{self.transcript}")
        self.assertIn("hello", row["summary"])


class TestSummaryCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.repo, self.pid = _fake_repo_with_project(Path(self.tmp))
        ensure_project_dir(self.pid)
        save_project_config({}, str(self.repo))

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

    def test_use_builtin_claude_preset(self) -> None:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_trace.cli",
                "summary",
                "use",
                "claude-summary",
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
        self.assertEqual(sm.get("command"), "agent-trace summary preset-run claude-summary")

    def test_use_builtin_ollama_preset_with_model(self) -> None:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_trace.cli",
                "summary",
                "use",
                "ollama-summary",
                "--model",
                "qwen2.5-coder:7b",
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
        self.assertEqual(
            sm.get("command"),
            "agent-trace summary preset-run ollama-summary --model qwen2.5-coder:7b",
        )


class TestSummaryPresetHelpers(unittest.TestCase):
    def test_build_preset_command_defaults(self) -> None:
        self.assertEqual(
            build_preset_command("ollama-summary"),
            "agent-trace summary preset-run ollama-summary --model llama3.1:8b",
        )


class TestRunSummaryGenerate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _tmp_dir()
        self._p = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmp})
        self._p.start()
        self.repo, self.pid = _fake_repo_with_project(Path(self.tmp))
        ensure_project_dir(self.pid)
        save_project_config(
            {"summary": {"enabled": True, "command": "cat"}},
            str(self.repo),
        )
        self.transcript = Path(self.tmp) / "g-transcript.jsonl"
        self.transcript.write_text("session content here")
        self.url = f"file://{self.transcript}"
        get_traces_path(self.pid).write_text(
            json.dumps(_trace_with_url("g1", "sess-gen", self.url)) + "\n",
        )

    def tearDown(self) -> None:
        self._p.stop()

    def test_generate_by_session_id(self) -> None:
        out = run_summary_generate(str(self.repo), session_id="sess-gen")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out[self.url], "session content here")
        path = get_session_summaries_path(self.pid)
        self.assertIn("session content here", path.read_text())

    def test_generate_by_url(self) -> None:
        out = run_summary_generate(str(self.repo), conversation_url=self.url)
        self.assertEqual(out, {self.url: "session content here"})
