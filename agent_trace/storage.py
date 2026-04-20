"""
Central path resolution for agent-trace.

All per-project runtime data lives under ``<AGENT_TRACE_HOME>/projects/<project_id>/``.
``project_id`` is derived from the canonical git repo root (absolute path with ``/``
replaced by ``-``), so no in-repo pointer is needed — every invocation can
recompute it from the working directory.

``AGENT_TRACE_HOME`` overrides the default ``~/.agent-trace`` (used by tests).
"""

from __future__ import annotations

import os
from pathlib import Path


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
    """Defensive sanitization — path-derived ids are already dash-only,
    but anything unexpected (``:``, ``\\``, residual ``/``) gets flattened."""
    return project_id.replace(":", "_").replace("/", "-").replace("\\", "-")


def path_to_project_id(repo_root: str) -> str:
    """Derive a stable project_id from an absolute repo path.

    ``/Users/jane/Desktop/foo`` → ``-Users-jane-Desktop-foo`` (Claude-Code convention).
    """
    canon = os.path.realpath(repo_root)
    return canon.replace(os.sep, "-")


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


def get_session_summaries_path(project_id: str) -> Path:
    """Append-only JSONL of per-session LLM summaries."""
    return get_project_dir(project_id) / "session-summaries.jsonl"


def get_project_config_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "project-config.json"


def get_attribution_state_path(project_id: str) -> Path:
    """Per-project attribution-window cursor (last commit timestamp seen)."""
    return get_project_dir(project_id) / "attribution-state.json"


# -------------------------------------------------------------------
# Repo-dir → project_id resolution
# -------------------------------------------------------------------

def resolve_project_id(
    repo_dir: str | os.PathLike[str] | None,
    *,
    create: bool = False,
) -> str | None:
    """Resolve a deterministic ``project_id`` for a repo directory.

    Uses the git repo root when available (so subdirectories inside a repo
    resolve to the same id as the root). Falls back to the given directory's
    real path if it's not inside a git repo.

    When ``create`` is True, the registry gets a metadata entry (first commit,
    origin url, known_roots) — useful for ``agent-trace projects``. When False,
    returns an id without touching the registry.
    """
    if repo_dir is None:
        return None

    from .trace import git_repo_root_for_path

    root = git_repo_root_for_path(str(repo_dir))
    if not root:
        root = os.path.realpath(str(repo_dir))

    pid = path_to_project_id(root)

    if create:
        from .registry import register_project_metadata

        register_project_metadata(root, pid)

    return pid
