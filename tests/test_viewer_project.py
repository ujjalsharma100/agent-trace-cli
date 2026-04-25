"""Tests for viewer /api/project metadata (Phase 8)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_trace.config import save_project_config

_BACKEND = Path(__file__).resolve().parent.parent / "viewer" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from routes.project import get_project_info  # noqa: E402


def _tmp_dir() -> str:
    root = Path(__file__).resolve().parent.parent / ".pytest_agent_trace_tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(dir=root)


class TestViewerProjectInfo(unittest.TestCase):
    def test_get_project_info_uses_pointer_not_legacy_config(self) -> None:
        from viewer.backend.routes.project import get_project_info

        base = Path(_tmp_dir())
        home = base / "gh"
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

        info = get_project_info(str(repo))
        self.assertTrue(info.get("has_agent_trace"))
        self.assertTrue(info.get("project_id"))
        self.assertTrue(str(info.get("agent_trace_home", "")))
        self.assertIn("projects", str(info.get("project_data_dir", "")))
