"""``agent-trace init`` prerequisites."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from agent_trace.cli import cmd_init


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


if __name__ == "__main__":
    unittest.main()
