"""
Central path resolution for agent-trace (Phase 2).

All per-project runtime data lives under ``<AGENT_TRACE_HOME>/projects/<project_id>/``.
The repo keeps only a tiny ``.agent-trace/project.json`` pointer containing the
stable ``project_id`` (resolved at init time).

``AGENT_TRACE_HOME`` overrides the default ``~/.agent-trace`` (used by tests).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


IN_REPO_DIR_NAME = ".agent-trace"
IN_REPO_POINTER_NAME = "project.json"
POINTER_VERSION = "1.0"


def get_agent_trace_home() -> Path:
    """Root of all global agent-trace state (respects ``AGENT_TRACE_HOME``)."""
    env = os.environ.get("AGENT_TRACE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".agent-trace"


def get_global_config_file() -> Path:
    return get_agent_trace_home() / "config.json"


def get_projects_registry_file() -> Path:
    return get_agent_trace_home() / "projects.json"


def get_sessions_dir() -> Path:
    return get_agent_trace_home() / "sessions"


def get_detached_base_dir() -> Path:
    return get_agent_trace_home() / "detached"


def _sanitize_id(project_id: str) -> str:
    """Make a project_id safe for use as a directory name."""
    return project_id.replace(":", "_").replace("/", "_").replace("\\", "_")


def get_project_dir(project_id: str) -> Path:
    return get_agent_trace_home() / "projects" / _sanitize_id(project_id)


def ensure_project_dir(project_id: str) -> Path:
    d = get_project_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# -------------------------------------------------------------------
# Per-project file paths
# -------------------------------------------------------------------

def get_traces_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "traces.jsonl"


def get_ledgers_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "ledgers.jsonl"


def get_commit_links_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "commit-links.jsonl"


def get_session_state_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "session-state.json"


def get_project_config_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "project-config.json"


# -------------------------------------------------------------------
# In-repo pointer (tiny .agent-trace/project.json, checked in)
# -------------------------------------------------------------------

def get_in_repo_pointer_path(repo_dir: str | os.PathLike[str]) -> Path:
    return Path(repo_dir) / IN_REPO_DIR_NAME / IN_REPO_POINTER_NAME


def read_in_repo_pointer(repo_dir: str | os.PathLike[str]) -> dict | None:
    p = get_in_repo_pointer_path(repo_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def write_in_repo_pointer(repo_dir: str | os.PathLike[str], project_id: str) -> None:
    p = get_in_repo_pointer_path(repo_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": POINTER_VERSION, "project_id": project_id}
    p.write_text(json.dumps(payload, indent=2) + "\n")


# -------------------------------------------------------------------
# Repo-dir → project_id resolution
# -------------------------------------------------------------------

def resolve_project_id(
    repo_dir: str | os.PathLike[str] | None,
    *,
    create: bool = False,
) -> str | None:
    """Resolve a ``project_id`` for a repo directory.

    Priority:
      1. In-repo pointer (``.agent-trace/project.json``).
      2. Registry lookup by canonical repo root.
      3. If ``create``, register a new project_id.
    """
    if repo_dir is None:
        return None
    repo_dir = str(repo_dir)

    ptr = read_in_repo_pointer(repo_dir)
    if ptr:
        pid = ptr.get("project_id")
        if isinstance(pid, str) and pid:
            return pid

    from .trace import git_repo_root_for_path
    from .registry import lookup_or_create_project_id, lookup_project_id_by_path

    root = git_repo_root_for_path(repo_dir) or os.path.realpath(repo_dir)
    pid = lookup_project_id_by_path(root)
    if pid:
        return pid
    if create:
        return lookup_or_create_project_id(root)
    return None
