"""
Hook configuration for Cursor and Claude Code.

Writes the correct hooks JSON so that agent events pipe through
``agent-trace record`` automatically.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


CURSOR_HOOKS_FILE = ".cursor/hooks.json"
CLAUDE_SETTINGS_FILE = ".claude/settings.json"

AGENT_TRACE_CMD = "agent-trace record"


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
