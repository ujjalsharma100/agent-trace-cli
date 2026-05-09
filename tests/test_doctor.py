"""Tests for agent-trace doctor (Phase 8)."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace.cli import cmd_doctor
from agent_trace.config import get_global_config_file, get_project_config, save_project_config


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
        save_project_config({})

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
        save_project_config({})
        cfg = get_project_config()
        assert cfg is not None
        cfg["summary"] = {"enabled": True, "command": ""}
        save_project_config(cfg)

        with self.assertRaises(SystemExit) as ctx:
            cmd_doctor(type("Args", (), {})())
        self.assertEqual(ctx.exception.code, 1)

    def test_doctor_fix_dry_run_global_config_lists_chmod(self) -> None:
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
        save_project_config({})

        gf = get_global_config_file()
        gf.parent.mkdir(parents=True, exist_ok=True)
        gf.write_text("{}\n")
        os.chmod(gf, 0o644)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_doctor(argparse.Namespace(fix=True, dry_run=True, yes=False))
        out = buf.getvalue()
        self.assertIn("[dry-run] would: chmod 600", out)
        self.assertEqual(gf.stat().st_mode & 0o777, 0o644)

    def test_doctor_fix_applies_global_config_chmod(self) -> None:
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
        save_project_config({})

        gf = get_global_config_file()
        gf.parent.mkdir(parents=True, exist_ok=True)
        gf.write_text("{}\n")
        os.chmod(gf, 0o644)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_doctor(argparse.Namespace(fix=True, dry_run=False, yes=True))
        self.assertEqual(gf.stat().st_mode & 0o777, 0o600)
        self.assertIn("Applied: chmod 600", buf.getvalue())

    def test_doctor_fix_installs_git_hooks_when_missing(self) -> None:
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
        save_project_config({})

        hook = repo / ".git" / "hooks" / "post-commit"
        if hook.exists():
            hook.unlink()

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_doctor(argparse.Namespace(fix=True, dry_run=False, yes=True))

        self.assertTrue(hook.is_file())
        self.assertIn("agent-trace commit-link", hook.read_text())
        self.assertIn("Applied: install git post-commit + post-rewrite hooks", buf.getvalue())

    def test_doctor_fix_dry_run_notes_refspec_when_origin_present(self) -> None:
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
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://example.com/x.git"],
            check=True,
            capture_output=True,
        )

        os.environ["AGENT_TRACE_HOME"] = str(home)
        os.chdir(repo)
        save_project_config({})

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_doctor(argparse.Namespace(fix=True, dry_run=True, yes=False))
        out = buf.getvalue()
        self.assertIn("[dry-run] would: configure git notes refspec for remote.origin", out)

    def test_doctor_fix_action_lines_are_sorted(self) -> None:
        """Stable ordering for doctor --fix output (M0 exit criterion)."""
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
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://example.com/x.git"],
            check=True,
            capture_output=True,
        )

        os.environ["AGENT_TRACE_HOME"] = str(home)
        os.chdir(repo)
        save_project_config({})

        gf = get_global_config_file()
        gf.parent.mkdir(parents=True, exist_ok=True)
        gf.write_text("{}\n")
        os.chmod(gf, 0o644)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_doctor(argparse.Namespace(fix=True, dry_run=True, yes=False))
        text = buf.getvalue()
        start = text.index("agent-trace doctor --fix")
        block = text[start:]
        lines = [ln for ln in block.splitlines() if ln.strip().startswith("[dry-run]")]
        self.assertGreater(len(lines), 1)
        sorted_lines = sorted(lines)
        self.assertEqual(lines, sorted_lines)
