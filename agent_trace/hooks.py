"""
Hook configuration for Cursor, Claude Code, and Git post-commit.

Writes the correct hooks JSON so that agent events pipe through
``agent-trace record`` automatically.  Also installs a git post-commit
hook that links commits to AI traces.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


CURSOR_HOOKS_FILE = ".cursor/hooks.json"
CLAUDE_SETTINGS_FILE = ".claude/settings.json"

AGENT_TRACE_CMD = "agent-trace record"
AGENT_TRACE_COMMIT_LINK_CMD = "agent-trace commit-link"

GIT_HOOK_MARKER = "agent-trace commit-link"
GIT_HOOK_SCRIPT = """\
# agent-trace: link commit to AI traces
agent-trace commit-link 2>/dev/null || true
"""


# -------------------------------------------------------------------
# Cursor
# -------------------------------------------------------------------

def configure_cursor_hooks(project_dir: str | None = None) -> bool:
    """Merge agent-trace into .cursor/hooks.json.  Returns True on success."""
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

def configure_claude_hooks(project_dir: str | None = None) -> bool:
    """Merge agent-trace into .claude/settings.json.  Returns True on success."""
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
    return True
