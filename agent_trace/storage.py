"""
Central path resolution for agent-trace.

All per-project runtime data lives under ``<AGENT_TRACE_HOME>/projects/<project_id>/``.

``project_id`` is anchored to the repo's ``.git`` directory: a single-line
``agent-trace-id`` file under ``git rev-parse --git-common-dir``. Worktrees
of the same repo share the anchor (and therefore the data directory), and a
rename of the repo on disk preserves the id since the file lives inside
``.git``. Re-clones legitimately get a fresh id — fresh clones rely on
``refs/notes/agent-trace`` for attribution, not on local data.

When the anchor doesn't exist yet, we fall back to a path-derived id so
``status`` / ``blame`` from un-init'd repos still resolve to *something*.
``init`` and any other ``create=True`` resolution will write the anchor.

``AGENT_TRACE_HOME`` overrides the default ``~/.agent-trace`` (used by tests).
"""

from __future__ import annotations

import os
import subprocess
import uuid
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
    """Derive a project_id from an absolute repo path (fallback only).

    ``/Users/jane/Desktop/foo`` → ``-Users-jane-Desktop-foo`` (Claude-Code convention).
    Used when no ``.git/agent-trace-id`` anchor exists. Once a project is
    initialised, the anchor takes over and the path is no longer load-bearing.
    """
    canon = os.path.realpath(repo_root)
    return canon.replace(os.sep, "-")


# -------------------------------------------------------------------
# Anchor (.git/agent-trace-id) — stable, worktree-aware project identity
# -------------------------------------------------------------------

ANCHOR_FILENAME = "agent-trace-id"


def _git_common_dir(repo_dir: str) -> str | None:
    """Return the shared ``.git`` directory for ``repo_dir``.

    For ordinary repos this is ``<repo>/.git``. For linked worktrees,
    ``--git-common-dir`` resolves to the main repo's ``.git`` so all
    worktrees of the same repo share the anchor (and therefore the
    project_id).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_dir, capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        p = r.stdout.strip()
        if not p:
            return None
        if not os.path.isabs(p):
            p = os.path.join(os.path.realpath(repo_dir), p)
        return os.path.realpath(p)
    except Exception:
        return None


def _anchor_path(git_common_dir: str) -> Path:
    return Path(git_common_dir) / ANCHOR_FILENAME


def _read_anchor(git_common_dir: str) -> str | None:
    p = _anchor_path(git_common_dir)
    try:
        if not p.is_file():
            return None
        s = p.read_text().strip()
        return s or None
    except OSError:
        return None


def _generate_project_id() -> str:
    """Fresh opaque project_id. ``at-<32 hex>``; safe as a directory name."""
    return f"at-{uuid.uuid4().hex}"


def _write_anchor(git_common_dir: str, project_id: str) -> bool:
    try:
        p = _anchor_path(git_common_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(project_id + "\n")
        return True
    except OSError:
        return False


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
    """Resolve a stable ``project_id`` for a repo directory.

    Resolution order:
      1. ``.git/agent-trace-id`` anchor (shared across worktrees of the
         same repo). Survives ``mv`` of the repo on disk.
      2. With ``create=True`` and no anchor present: generate a fresh
         opaque id (``at-<32 hex>``) and write the anchor.
      3. Path-derived fallback (``/Users/x/foo`` → ``-Users-x-foo``) for
         un-init'd repos and non-git directories.

    When ``create`` is True the registry also gets a metadata entry.
    """
    if repo_dir is None:
        return None

    from .trace import git_repo_root_for_path

    root = git_repo_root_for_path(str(repo_dir))

    if root:
        # Inside a git repo — try the anchor.
        common = _git_common_dir(root) or os.path.join(root, ".git")
        pid = _read_anchor(common)
        if pid is None and create:
            pid = _generate_project_id()
            _write_anchor(common, pid)
        if pid is None:
            # Un-init'd repo: behave like the legacy path-derived form so
            # status/blame from a fresh checkout still resolves.
            pid = path_to_project_id(root)

        if create:
            from .registry import register_project_metadata

            register_project_metadata(root, pid)
        return pid

    # Not inside a git repo at all — keep the path-derived id (used by
    # detached-edit handling and tests for non-repo paths).
    real = os.path.realpath(str(repo_dir))
    pid = path_to_project_id(real)
    if create:
        from .registry import register_project_metadata

        register_project_metadata(real, pid)
    return pid
