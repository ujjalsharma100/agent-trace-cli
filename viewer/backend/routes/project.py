"""
/api/project — return project root, storage mode, and whether agent-trace is initialized.
"""
from __future__ import annotations

import json
import os


def get_project_info(project_root: str) -> dict:
    """Return { root, storage, has_agent_trace } for the project."""
    root = os.path.abspath(project_root)
    config_path = os.path.join(root, ".agent-trace", "config.json")
    has_agent_trace = os.path.isfile(config_path)
    storage = "local"
    if has_agent_trace:
        try:
            with open(config_path) as f:
                config = json.load(f)
            storage = config.get("storage", "local")
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "root": root,
        "storage": storage,
        "has_agent_trace": has_agent_trace,
    }
