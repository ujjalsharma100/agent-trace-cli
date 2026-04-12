"""
/api/project — project root, agent-trace init state, and global storage paths.

Uses the in-repo pointer ``.agent-trace/project.json`` (Phase 2), not legacy config.json.
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


def get_project_info(project_root: str) -> dict[str, Any]:
    """Return project metadata for the viewer UI."""
    root = os.path.abspath(project_root)
    home = os.environ.get("AGENT_TRACE_HOME") or os.path.expanduser("~/.agent-trace")
    home = os.path.abspath(os.path.expanduser(home))

    pointer_path = os.path.join(root, ".agent-trace", "project.json")
    ptr = _read_json(pointer_path)
    project_id = ptr.get("project_id") if ptr else None
    if not isinstance(project_id, str):
        project_id = None

    has_agent_trace = bool(project_id)
    storage = "local"
    label = None
    project_data_dir = None

    if project_id:
        safe = project_id.replace(":", "_").replace("/", "_").replace("\\", "_")
        project_data_dir = os.path.join(home, "projects", safe)
        cfg_path = os.path.join(project_data_dir, "project-config.json")
        cfg = _read_json(cfg_path)
        if cfg:
            storage = str(cfg.get("storage", "local"))
            lab = cfg.get("label")
            if isinstance(lab, str) and lab.strip():
                label = lab.strip()

    note_head: dict[str, Any] | None = None
    if os.path.isdir(os.path.join(root, ".git")):
        note_head = _git_note_for_head(root)

    return {
        "root": root,
        "agent_trace_home": home,
        "storage": storage,
        "has_agent_trace": has_agent_trace,
        "project_id": project_id,
        "label": label,
        "project_data_dir": project_data_dir,
        "git_note_head": note_head,
    }
