"""``agent-trace init`` prerequisites and defaults."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_trace.cli import cmd_init
from agent_trace.config import get_project_config
from agent_trace.summary_presets import build_preset_command


def _tmp_dir() -> str:
    root = Path(__file__).resolve().parent.parent / ".pytest_agent_trace_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(dir=root)


def _git_init_no_commit(repo: str) -> None:
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


class TestInitRequiresCommit(unittest.TestCase):
    def test_init_fails_when_repo_has_no_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_no_commit(tmp)
            old = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaises(SystemExit) as ctx:
                    cmd_init(None)
                self.assertEqual(ctx.exception.code, 1)
            finally:
                os.chdir(old)


class TestInitDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_home = os.environ.get("AGENT_TRACE_HOME")
        self._prev_cwd = os.getcwd()

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("AGENT_TRACE_HOME", None)
        else:
            os.environ["AGENT_TRACE_HOME"] = self._prev_home
        os.chdir(self._prev_cwd)

    def test_init_writes_notes_and_ollama_summary_defaults(self) -> None:
        base = Path(_tmp_dir())
        home = base / "home"
        home.mkdir()
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
        (repo / "f.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

        os.environ["AGENT_TRACE_HOME"] = str(home)
        os.chdir(repo)
        cmd_init(None)

        cfg = get_project_config(str(repo))
        self.assertIsNotNone(cfg)
        assert cfg is not None
        notes = cfg.get("notes") or {}
        self.assertTrue(notes.get("all_session_conversations"))
        sm = cfg.get("summary") or {}
        self.assertTrue(sm.get("enabled"))
        self.assertEqual(sm.get("command"), build_preset_command("ollama-summary"))


if __name__ == "__main__":
    unittest.main()
