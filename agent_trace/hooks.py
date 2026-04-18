"""
Hook configuration for Cursor, Claude Code, and Git post-commit.

Writes the correct hooks JSON so that agent events pipe through
``agent-trace record`` automatically.  Also installs a git post-commit
hook that links commits to AI traces.

Hooks can be installed at two levels:

- **Project-level** — ``<project>/.cursor/hooks.json``, ``<project>/.claude/settings.json``
- **Global** — ``~/.cursor/hooks.json``, ``~/.claude/settings.json``

Global hooks fire for *every* project / directory, just like ``git config --global``.
The ``agent-trace record`` pipeline already resolves the correct project from the
file being edited (via its git root), so global hooks "just work": edits in an
initialised repo are recorded, and edits elsewhere are silently ignored.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


GIT_NOTES_REFSPEC = "+refs/notes/agent-trace:refs/notes/agent-trace"


CURSOR_HOOKS_FILE = ".cursor/hooks.json"
CLAUDE_SETTINGS_FILE = ".claude/settings.json"

CURSOR_GLOBAL_HOOKS_FILE = Path.home() / ".cursor" / "hooks.json"
CLAUDE_GLOBAL_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

AGENT_TRACE_CMD = "agent-trace record"
AGENT_TRACE_COMMIT_LINK_CMD = "agent-trace commit-link"

GIT_HOOK_MARKER = "agent-trace commit-link"
GIT_HOOK_SCRIPT = """\
# agent-trace: link commit to AI traces
agent-trace commit-link 2>/dev/null || true
"""

GIT_POST_REWRITE_MARKER = "agent-trace rewrite-ledger"
GIT_POST_REWRITE_SCRIPT = """\
# agent-trace: remap ledgers after rebase/amend
agent-trace rewrite-ledger 2>/dev/null || true
"""


# -------------------------------------------------------------------
# Cursor
# -------------------------------------------------------------------

def configure_cursor_hooks(project_dir: str | None = None, *, global_install: bool = False) -> bool:
    """Merge agent-trace into .cursor/hooks.json.  Returns True on success.

    When ``global_install`` is True, writes to ``~/.cursor/hooks.json`` instead
    of the project-level file.
    """
    if global_install:
        hooks_path = CURSOR_GLOBAL_HOOKS_FILE
    else:
        if project_dir is None:
            project_dir = os.getcwd()
        hooks_path = Path(project_dir) / CURSOR_HOOKS_FILE

    hooks_path.parent.mkdir(parents=True, exist_ok=True)

    if hooks_path.exists():
        try:
            config = json.loads(hooks_path.read_text())
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}

    config.setdefault("version", 1)
    config.setdefault("hooks", {})

    for event in (
        "sessionStart",
        "sessionEnd",
        "afterFileEdit",
        "afterTabFileEdit",
        "afterShellExecution",
        "afterAgentResponse",
    ):
        existing = config["hooks"].get(event, [])
        already = any(
            AGENT_TRACE_CMD in (h.get("command", "") if isinstance(h, dict) else "")
            for h in existing
        )
        if not already:
            existing.append({"command": AGENT_TRACE_CMD})
            config["hooks"][event] = existing

    hooks_path.write_text(json.dumps(config, indent=2) + "\n")
    return True


# -------------------------------------------------------------------
# Claude Code
# -------------------------------------------------------------------

def configure_claude_hooks(project_dir: str | None = None, *, global_install: bool = False) -> bool:
    """Merge agent-trace into .claude/settings.json.  Returns True on success.

    When ``global_install`` is True, writes to ``~/.claude/settings.json`` instead
    of the project-level file.
    """
    if global_install:
        settings_path = CLAUDE_GLOBAL_SETTINGS_FILE
    else:
        if project_dir is None:
            project_dir = os.getcwd()
        settings_path = Path(project_dir) / CLAUDE_SETTINGS_FILE

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            config = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            config = {}
    else:
        config = {}

    config.setdefault("hooks", {})

    hook_entry = {"type": "command", "command": AGENT_TRACE_CMD}

    # SessionStart / SessionEnd
    for event in ("SessionStart", "SessionEnd"):
        existing = config["hooks"].get(event, [])
        already = any(
            any(AGENT_TRACE_CMD in h.get("command", "") for h in entry.get("hooks", []))
            for entry in existing
            if isinstance(entry, dict)
        )
        if not already:
            existing.append({"hooks": [hook_entry]})
            config["hooks"][event] = existing

    # PostToolUse (with matchers)
    post = config["hooks"].get("PostToolUse", [])
    already = any(
        any(AGENT_TRACE_CMD in h.get("command", "") for h in entry.get("hooks", []))
        for entry in post
        if isinstance(entry, dict)
    )
    if not already:
        config["hooks"]["PostToolUse"] = [
            {"matcher": "Write|Edit", "hooks": [hook_entry]},
            {"matcher": "Bash", "hooks": [hook_entry]},
        ]

    # Stop — conversation sync after agent finishes (Claude Code equivalent of afterAgentResponse)
    stop = config["hooks"].get("Stop", [])
    already = any(
        any(AGENT_TRACE_CMD in h.get("command", "") for h in entry.get("hooks", []))
        for entry in stop
        if isinstance(entry, dict)
    )
    if not already:
        stop.append({"hooks": [hook_entry]})
        config["hooks"]["Stop"] = stop

    settings_path.write_text(json.dumps(config, indent=2) + "\n")
    return True


# -------------------------------------------------------------------
# Global hook detection and removal
# -------------------------------------------------------------------

def has_global_cursor_hooks() -> bool:
    """Return True if ``~/.cursor/hooks.json`` contains agent-trace hooks."""
    try:
        raw = CURSOR_GLOBAL_HOOKS_FILE.read_text()
    except (OSError, FileNotFoundError):
        return False
    return AGENT_TRACE_CMD in raw


def has_global_claude_hooks() -> bool:
    """Return True if ``~/.claude/settings.json`` contains agent-trace hooks."""
    try:
        raw = CLAUDE_GLOBAL_SETTINGS_FILE.read_text()
    except (OSError, FileNotFoundError):
        return False
    return AGENT_TRACE_CMD in raw


def has_global_hooks(tool: str | None = None) -> bool:
    """Return True if global hooks are configured.

    ``tool`` can be ``"cursor"``, ``"claude"``, or ``None`` (any).
    """
    if tool == "cursor":
        return has_global_cursor_hooks()
    if tool == "claude":
        return has_global_claude_hooks()
    return has_global_cursor_hooks() or has_global_claude_hooks()


def _remove_agent_trace_from_cursor(hooks_path: Path) -> bool:
    """Remove agent-trace entries from a Cursor hooks.json file."""
    if not hooks_path.is_file():
        return False
    try:
        config = json.loads(hooks_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        filtered = [
            h for h in entries
            if not (isinstance(h, dict) and AGENT_TRACE_CMD in h.get("command", ""))
        ]
        if len(filtered) != len(entries):
            changed = True
            if filtered:
                hooks[event] = filtered
            else:
                del hooks[event]

    if changed:
        hooks_path.write_text(json.dumps(config, indent=2) + "\n")
    return changed


def _remove_agent_trace_from_claude(settings_path: Path) -> bool:
    """Remove agent-trace entries from a Claude Code settings.json file."""
    if not settings_path.is_file():
        return False
    try:
        config = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        filtered = []
        for entry in entries:
            if not isinstance(entry, dict):
                filtered.append(entry)
                continue
            inner = entry.get("hooks", [])
            if isinstance(inner, list) and any(
                AGENT_TRACE_CMD in h.get("command", "")
                for h in inner if isinstance(h, dict)
            ):
                changed = True
                continue
            filtered.append(entry)
        if len(filtered) != len(entries):
            changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]

    if changed:
        if not hooks:
            del config["hooks"]
        config_text = json.dumps(config, indent=2) + "\n"
        settings_path.write_text(config_text)
    return changed


def remove_global_cursor_hooks() -> bool:
    """Remove agent-trace entries from ``~/.cursor/hooks.json``."""
    return _remove_agent_trace_from_cursor(CURSOR_GLOBAL_HOOKS_FILE)


def remove_global_claude_hooks() -> bool:
    """Remove agent-trace entries from ``~/.claude/settings.json``."""
    return _remove_agent_trace_from_claude(CLAUDE_GLOBAL_SETTINGS_FILE)


def setup_global_hooks(tools: list[str] | None = None) -> dict[str, bool]:
    """Install global hooks for the given tools (default: all).

    Returns a dict of ``{tool_name: success}``.
    """
    if tools is None:
        tools = ["cursor", "claude"]

    results: dict[str, bool] = {}
    for tool in tools:
        if tool == "cursor":
            results["cursor"] = configure_cursor_hooks(global_install=True)
        elif tool == "claude":
            results["claude"] = configure_claude_hooks(global_install=True)
    return results


def remove_global_hooks(tools: list[str] | None = None) -> dict[str, bool]:
    """Remove global hooks for the given tools (default: all).

    Returns a dict of ``{tool_name: removed}``.
    """
    if tools is None:
        tools = ["cursor", "claude"]

    results: dict[str, bool] = {}
    for tool in tools:
        if tool == "cursor":
            results["cursor"] = remove_global_cursor_hooks()
        elif tool == "claude":
            results["claude"] = remove_global_claude_hooks()
    return results


# -------------------------------------------------------------------
# Git post-commit hook
# -------------------------------------------------------------------

def configure_git_hooks(project_dir: str | None = None) -> bool:
    """Install agent-trace post-commit hook into .git/hooks/.

    Logic:
      1. If .git/hooks/post-commit already contains the marker, skip.
      2. If it exists with other content, append the agent-trace call.
      3. If it doesn't exist, create it with a shebang + the call.
      4. chmod +x the hook file.

    Returns True on success, False if .git directory is not found.
    """
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

        # Already installed — nothing to do
        if GIT_HOOK_MARKER in content:
            return True

        # Append to existing hook
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + GIT_HOOK_SCRIPT
        hook_path.write_text(content)
    else:
        # Create new hook file
        content = "#!/bin/sh\n" + GIT_HOOK_SCRIPT
        hook_path.write_text(content)

    # Ensure executable
    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Also install the post-rewrite hook for ledger remapping
    configure_git_post_rewrite_hook(project_dir)

    return True


def configure_git_post_rewrite_hook(project_dir: str | None = None) -> bool:
    """Install agent-trace post-rewrite hook into .git/hooks/.

    The post-rewrite hook is called by git after ``rebase`` or ``commit --amend``.
    It remaps ledger commit SHAs from old to new.

    Returns True on success, False if .git directory is not found.
    """
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
        content = "#!/bin/sh\n" + GIT_POST_REWRITE_SCRIPT
        hook_path.write_text(content)

    current = hook_path.stat().st_mode
    hook_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


# -------------------------------------------------------------------
# Git notes refspecs (Phase 5)
# -------------------------------------------------------------------


def configure_git_notes_refspecs(project_dir: str | None = None, remote_name: str = "origin") -> bool:
    """Add fetch/push refspecs for ``refs/notes/agent-trace`` on ``remote_name``.

    Skips if the remote does not exist or the refspecs are already present.
    """
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
