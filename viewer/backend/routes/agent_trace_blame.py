"""
/api/agent-trace-blame — run agent-trace blame (local or remote from config).

Reads project config; if storage == "local" uses CLI lib with project_dir;
if storage == "remote" POSTs to service /api/v1/blame. Returns same contract as CLI --json.
Requires ~/.agent-trace/lib on sys.path (added by main.py) to import agent_trace.
"""
from __future__ import annotations

import json
import os
from typing import Any


def get_agent_trace_blame(
    project_root: str,
    rel_path: str,
) -> tuple[dict[str, Any] | None, str | None, int]:
    """
    Run agent-trace blame for the file at rel_path under project_root.

    Uses agent_trace.blame and agent_trace.config when ~/.agent-trace/lib is on sys.path.
    - Resolves path under project_root; returns (None, error_message, 400/404) if invalid.
    - Returns (result_dict, None, 200) on success; result has "file" and "attributions".
    - Returns (None, error_message, 503) if agent-trace lib is not available.
    """
    root = os.path.abspath(project_root)
    full = os.path.normpath(os.path.join(root, rel_path.lstrip("/")))
    if not full.startswith(root):
        return None, "path outside project", 400
    if not os.path.isfile(full):
        return None, "file not found", 404

    try:
        from agent_trace import blame as blame_module  # type: ignore[import-untyped]
    except ImportError:
        return (
            None,
            "agent-trace lib not available (install CLI or set ~/.agent-trace/lib)",
            503,
        )

    # rel_path for blame: relative to project root (CLI uses cwd = project_dir)
    file_path_for_blame = rel_path.lstrip("/")
    try:
        result_json = blame_module.blame_file(
            file_path_for_blame,
            json_output=True,
            project_dir=project_root,
        )
    except TypeError:
        # Older CLI without project_dir: change cwd and call without it
        orig_cwd = os.getcwd()
        try:
            os.chdir(project_root)
            result_json = blame_module.blame_file(
                file_path_for_blame,
                json_output=True,
            )
        finally:
            os.chdir(orig_cwd)
    if result_json is None:
        return None, "blame failed (not a git repo or no blame data)", 404

    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return None, "invalid blame output", 500
    return data, None, 200
