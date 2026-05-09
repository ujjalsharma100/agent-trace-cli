from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

GIT_NOTES_REFSPEC = "+refs/notes/agent-trace:refs/notes/agent-trace"

AGENT_TRACE_COMMIT_LINK_CMD = "agent-trace commit-link"

GIT_HOOK_MARKER = AGENT_TRACE_COMMIT_LINK_CMD
GIT_HOOK_SCRIPT = """\
# agent-trace: link commit to AI traces
agent-trace commit-link 2>/dev/null || true
"""

GIT_POST_REWRITE_MARKER = "agent-trace rewrite-ledger"
GIT_POST_REWRITE_SCRIPT = """\
# agent-trace: remap ledgers after rebase/amend
agent-trace rewrite-ledger 2>/dev/null || true
"""


def configure_git_hooks(project_dir: str | None = None) -> bool:
    if project_dir is None:
        project_dir = os.getcwd()

    git_dir = Path(project_dir) / ".git"
    if not git_dir.is_dir():
        return False

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-commit"

    if hook_path.exists():
        try:
            content = hook_path.read_text()
        except OSError:
            return False

        if GIT_HOOK_MARKER in content:
            return True

        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + GIT_HOOK_SCRIPT
        hook_path.write_text(content)
    else:
        hook_path.write_text("#!/bin/sh\n" + GIT_HOOK_SCRIPT)

    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    configure_git_post_rewrite_hook(project_dir)
    return True


def configure_git_post_rewrite_hook(project_dir: str | None = None) -> bool:
    if project_dir is None:
        project_dir = os.getcwd()

    git_dir = Path(project_dir) / ".git"
    if not git_dir.is_dir():
        return False

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "post-rewrite"

    if hook_path.exists():
        try:
            content = hook_path.read_text()
        except OSError:
            return False

        if GIT_POST_REWRITE_MARKER in content:
            return True

        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + GIT_POST_REWRITE_SCRIPT
        hook_path.write_text(content)
    else:
        hook_path.write_text("#!/bin/sh\n" + GIT_POST_REWRITE_SCRIPT)

    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def configure_git_notes_refspecs(project_dir: str | None = None, remote_name: str = "origin") -> bool:
    if project_dir is None:
        project_dir = os.getcwd()

    git_dir = Path(project_dir) / ".git"
    if not git_dir.is_dir():
        return False

    try:
        r = subprocess.run(
            ["git", "-C", project_dir, "remote", "get-url", remote_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return False
    except Exception:
        return False

    for key in ("fetch", "push"):
        try:
            cur = subprocess.run(
                ["git", "-C", project_dir, "config", "--get-all", f"remote.{remote_name}.{key}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            existing = cur.stdout if cur.returncode == 0 else ""
        except Exception:
            existing = ""
        if "refs/notes/agent-trace" in existing:
            continue
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    project_dir,
                    "config",
                    "--add",
                    f"remote.{remote_name}.{key}",
                    GIT_NOTES_REFSPEC,
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return False
    return True
