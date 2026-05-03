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
    """Path-derived project_id (legacy fallback when no anchor exists)."""
    return os.path.realpath(repo_root).replace(os.sep, "-")


def _read_anchor_id(repo_root: str) -> str | None:
    """Resolve the ``.git/agent-trace-id`` anchor for ``repo_root``.

    Mirrors ``agent_trace.storage._read_anchor`` so the viewer stays
    self-contained (no import of the CLI package).
    """
    try:
        r = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        gd = r.stdout.strip()
        if not gd:
            return None
        if not os.path.isabs(gd):
            gd = os.path.join(os.path.realpath(repo_root), gd)
        anchor = os.path.join(gd, "agent-trace-id")
        if not os.path.isfile(anchor):
            return None
        with open(anchor, encoding="utf-8") as f:
            s = f.read().strip()
        return s or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _resolve_project_id(repo_root: str) -> str:
    """Anchor-first id resolution; path-derived fallback."""
    anchored = _read_anchor_id(repo_root)
    if anchored:
        return anchored
    return _path_to_project_id(repo_root)


def get_project_info(project_root: str) -> dict[str, Any]:
    """Return project metadata for the viewer UI."""
    root = os.path.abspath(project_root)
    home = os.environ.get("AGENT_TRACE_HOME") or os.path.expanduser("~/.agent-trace")
    home = os.path.abspath(os.path.expanduser(home))

    project_id = _resolve_project_id(root)
    project_data_dir = os.path.join(home, "projects", project_id)
    cfg_path = os.path.join(project_data_dir, "project-config.json")
    cfg = _read_json(cfg_path)

    has_agent_trace = cfg is not None
    note_head: dict[str, Any] | None = None
    if os.path.isdir(os.path.join(root, ".git")):
        note_head = _git_note_for_head(root)

    return {
        "root": root,
        "agent_trace_home": home,
        "has_agent_trace": has_agent_trace,
        "project_id": project_id if has_agent_trace else None,
        "project_data_dir": project_data_dir if has_agent_trace else None,
        "git_note_head": note_head,
    }
