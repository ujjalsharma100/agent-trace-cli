"""
/api/project — project root, agent-trace init state, and global storage paths.

Resolves ``project_id`` directly from the repo root (sanitized absolute path).
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _git_note_for_head(project_root: str) -> dict[str, Any] | None:
    """Return parsed JSON git note for HEAD on refs/notes/agent-trace, or None."""
    try:
        r = subprocess.run(
            [
                "git",
                "-C",
                project_root,
                "notes",
                "--ref",
                "agent-trace",
                "show",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _path_to_project_id(repo_root: str) -> str:
    """Mirror of ``agent_trace.storage.path_to_project_id`` (keeps viewer standalone)."""
    return os.path.realpath(repo_root).replace(os.sep, "-")


def get_project_info(project_root: str) -> dict[str, Any]:
    """Return project metadata for the viewer UI."""
    root = os.path.abspath(project_root)
    home = os.environ.get("AGENT_TRACE_HOME") or os.path.expanduser("~/.agent-trace")
    home = os.path.abspath(os.path.expanduser(home))

    project_id = _path_to_project_id(root)
    project_data_dir = os.path.join(home, "projects", project_id)
    cfg_path = os.path.join(project_data_dir, "project-config.json")
    cfg = _read_json(cfg_path)

    has_agent_trace = cfg is not None
    label = None
    if cfg:
        lab = cfg.get("label")
        if isinstance(lab, str) and lab.strip():
            label = lab.strip()

    note_head: dict[str, Any] | None = None
    if os.path.isdir(os.path.join(root, ".git")):
        note_head = _git_note_for_head(root)

    return {
        "root": root,
        "agent_trace_home": home,
        "has_agent_trace": has_agent_trace,
        "project_id": project_id if has_agent_trace else None,
        "label": label,
        "project_data_dir": project_data_dir if has_agent_trace else None,
        "git_note_head": note_head,
    }
