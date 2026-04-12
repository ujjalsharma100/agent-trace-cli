"""Tests for agent-trace doctor (Phase 8)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_trace.cli import cmd_doctor
from agent_trace.config import get_project_config, save_project_config


def _tmp_dir() -> str:
    root = Path(__file__).resolve().parent.parent / ".pytest_agent_trace_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(dir=root)


class TestDoctor(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_home = os.environ.get("AGENT_TRACE_HOME")
        self._prev_cwd = os.getcwd()

    def tearDown(self) -> None:
        if self._prev_home is None:
            os.environ.pop("AGENT_TRACE_HOME", None)
        else:
            os.environ["AGENT_TRACE_HOME"] = self._prev_home
        os.chdir(self._prev_cwd)

    def test_doctor_exits_zero_for_minimal_project(self) -> None:
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
        save_project_config({"storage": "local", "label": "doc-test"})

        cmd_doctor(type("Args", (), {})())

    def test_doctor_exits_nonzero_when_summary_enabled_but_empty_command(self) -> None:
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
        save_project_config({"storage": "local", "label": "x"})
        cfg = get_project_config()
        assert cfg is not None
        cfg["summary"] = {"enabled": True, "command": ""}
        save_project_config(cfg)

        with self.assertRaises(SystemExit) as ctx:
            cmd_doctor(type("Args", (), {})())
        self.assertEqual(ctx.exception.code, 1)
