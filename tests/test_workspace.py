"""Phase 1b: file-anchored project resolution and registry."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace import registry as registry_mod
from agent_trace.registry import lookup_or_create_project_id
from agent_trace.trace import (
    cli_resolve_project_root,
    git_repo_root_for_path,
    resolve_file_project,
)


def _git_init_with_commit(repo: str) -> None:
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
    Path(repo, "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


class TestResolveFileProject(unittest.TestCase):
    def test_subfolder_launch_edits_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            sub = Path(tmp) / "sub"
            sub.mkdir()
            root_file = Path(tmp) / "root.py"
            root_file.write_text("a\n")
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                res = resolve_file_project(str(root_file))
            self.assertIsNotNone(res)
            assert res is not None
            self.assertEqual(res.repo_root, os.path.realpath(tmp))
            self.assertEqual(res.rel_path, "root.py")

    def test_nested_repo_prefers_inner(self) -> None:
        with tempfile.TemporaryDirectory() as outer:
            inner = Path(outer) / "inner"
            inner.mkdir()
            _git_init_with_commit(str(inner))
            inner_file = inner / "x.py"
            inner_file.write_text("1\n")
            with patch.object(registry_mod, "PROJECTS_FILE", Path(outer) / "projects.json"):
                res = resolve_file_project(str(inner_file))
            self.assertIsNotNone(res)
            assert res is not None
            self.assertEqual(res.repo_root, os.path.realpath(str(inner)))

    def test_detached_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nf = Path(tmp) / "n.py"
            nf.write_text("z")
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                with patch(
                    "agent_trace.config.get_global_config",
                    return_value={"capture_detached_edits": True},
                ):
                    res = resolve_file_project(str(nf))
            self.assertIsNotNone(res)
            assert res is not None
            self.assertTrue(res.project_id.startswith("detached:"))
            self.assertTrue(res.is_detached)

    def test_detached_skipped_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            nf = Path(tmp) / "orphan.py"
            nf.write_text("z")
            with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                res = resolve_file_project(str(nf))
            self.assertIsNone(res)


class TestRegistry(unittest.TestCase):
    def test_id_is_path_derived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r1 = Path(tmp) / "r1"
            r1.mkdir()
            _git_init_with_commit(str(r1))
            reg = Path(tmp) / "registry.json"
            with patch.object(registry_mod, "PROJECTS_FILE", reg):
                a = lookup_or_create_project_id(str(r1))
            # project_id = sanitized absolute path (Claude-Code convention)
            self.assertEqual(a, os.path.realpath(str(r1)).replace(os.sep, "-"))
            # Moving the repo changes the path, hence the id — just like ``git init``.
            r2 = Path(tmp) / "r2"
            os.rename(r1, r2)
            with patch.object(registry_mod, "PROJECTS_FILE", reg):
                b = lookup_or_create_project_id(str(r2))
            self.assertEqual(b, os.path.realpath(str(r2)).replace(os.sep, "-"))
            self.assertNotEqual(a, b)


class TestGitRepoRootForPath(unittest.TestCase):
    def test_file_anchored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            p = Path(tmp) / "a.py"
            p.write_text("x")
            g = git_repo_root_for_path(str(p))
            self.assertEqual(g, os.path.realpath(tmp))


class TestCliResolve(unittest.TestCase):
    def test_resolve_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            r = cli_resolve_project_root(tmp, cwd="/")
            self.assertEqual(r, os.path.realpath(tmp))


if __name__ == "__main__":
    unittest.main()
