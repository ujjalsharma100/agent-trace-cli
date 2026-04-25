"""
Configuration management for agent-trace.

Two-tier layout:

  1. Global       — <AGENT_TRACE_HOME>/config.json (global auth token, preferences)
  2. Per-project  — <AGENT_TRACE_HOME>/projects/<id>/project-config.json
                    (notes.*, summary.*, remote.default)

Project identity is derived from the git repo root (a sanitized absolute path),
so no in-repo state is needed. Existence of the project-config.json file under
the global project dir determines whether a repo is "initialized".

``AGENT_TRACE_HOME`` env var overrides the default ``~/.agent-trace`` (used by tests).

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .storage import (
    ensure_project_dir,
    get_agent_trace_home,
    get_global_config_file,
    get_project_config_path,
    resolve_project_id,
)


# -------------------------------------------------------------------
# Back-compat shims (computed lazily so tests can override AGENT_TRACE_HOME)
# -------------------------------------------------------------------

class _GlobalConfigDirProxy:
    """Acts like the old ``GLOBAL_CONFIG_DIR`` Path but re-reads env each use."""

    def __fspath__(self) -> str:
        return os.fspath(get_agent_trace_home())

    def __str__(self) -> str:
        return str(get_agent_trace_home())

    def __truediv__(self, other: str) -> Path:
        return get_agent_trace_home() / other

    def mkdir(self, *args, **kwargs):  # noqa: D401
        return get_agent_trace_home().mkdir(*args, **kwargs)


GLOBAL_CONFIG_DIR = _GlobalConfigDirProxy()


# -------------------------------------------------------------------
# Global config
# -------------------------------------------------------------------

def get_global_config() -> dict:
    """Load <AGENT_TRACE_HOME>/config.json (returns {} if missing)."""
    f = get_global_config_file()
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_global_config(config: dict) -> None:
    """Write <AGENT_TRACE_HOME>/config.json."""
    f = get_global_config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(config, indent=2) + "\n")
    try:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


# -------------------------------------------------------------------
# Project config (lives in the global project dir, keyed by project_id)
# -------------------------------------------------------------------

def get_project_config(project_dir: str | None = None) -> dict | None:
    """Load per-project settings.  Returns ``None`` when the project is not initialised."""
    if project_dir is None:
        project_dir = os.getcwd()

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None

    cfg_path = get_project_config_path(pid)
    if cfg_path.is_file():
        try:
            return json.loads(cfg_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_project_config(config: dict, project_dir: str | None = None) -> None:
    """Persist per-project settings under ``<AGENT_TRACE_HOME>/projects/<id>/``."""
    if project_dir is None:
        project_dir = os.getcwd()

    pid = resolve_project_id(project_dir, create=True)
    if not pid:
        raise RuntimeError(
            f"agent-trace: cannot resolve project_id for {project_dir} "
            "(not a git repository and no registry entry)",
        )

    ensure_project_dir(pid)
    cfg_path = get_project_config_path(pid)
    cfg_path.write_text(json.dumps(config, indent=2) + "\n")


# -------------------------------------------------------------------
# Auth token resolution (global only — projects don't store tokens anymore)
# -------------------------------------------------------------------

def get_auth_token() -> str | None:
    """Resolve auth token: env → global config."""
    env = os.environ.get("AGENT_TRACE_TOKEN")
    if env:
        return env

    global_cfg = get_global_config()
    if global_cfg.get("auth_token"):
        return global_cfg["auth_token"]

    return None
