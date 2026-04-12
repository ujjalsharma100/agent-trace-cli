"""
Configuration management for agent-trace (Phase 2).

Three-tier layout:

  1. Global            — <AGENT_TRACE_HOME>/config.json (tokens, defaults)
  2. Per-project       — <AGENT_TRACE_HOME>/projects/<id>/project-config.json
                         (storage mode, service_url, auth_token, notes.*, summary.*)
  3. In-repo pointer   — <repo>/.agent-trace/project.json (stable project_id)

``AGENT_TRACE_HOME`` env var overrides the default ``~/.agent-trace`` (used by tests).

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .storage import (
    IN_REPO_DIR_NAME,
    IN_REPO_POINTER_NAME,
    ensure_project_dir,
    get_agent_trace_home,
    get_global_config_file,
    get_project_config_path,
    resolve_project_id,
    write_in_repo_pointer,
)


# -------------------------------------------------------------------
# Load .env from the CLI tool's install directory (if present)
# -------------------------------------------------------------------

def _load_dotenv():
    """Read key=value pairs from the .env next to the installed lib."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            os.environ.setdefault(key, value)
    except OSError:
        pass

_load_dotenv()


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

PROJECT_CONFIG_DIR_NAME = IN_REPO_DIR_NAME
PROJECT_CONFIG_FILE_NAME = IN_REPO_POINTER_NAME

DEFAULT_SERVICE_URL = os.environ.get("AGENT_TRACE_URL", "http://localhost:5000").rstrip("/")


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
# Project config (lives in the global project dir)
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
    """Persist per-project settings and the in-repo pointer.

    Resolves (or creates) a ``project_id`` for the repo, writes the settings to
    ``<AGENT_TRACE_HOME>/projects/<id>/project-config.json``, and drops a
    tiny pointer at ``<repo>/.agent-trace/project.json``.
    """
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

    write_in_repo_pointer(project_dir, pid)


# -------------------------------------------------------------------
# Auth token resolution
# -------------------------------------------------------------------

def get_auth_token(project_config: dict | None = None) -> str | None:
    """Resolve auth token: env → global → project."""
    env = os.environ.get("AGENT_TRACE_TOKEN")
    if env:
        return env

    global_cfg = get_global_config()
    if global_cfg.get("auth_token"):
        return global_cfg["auth_token"]

    if project_config and project_config.get("auth_token"):
        return project_config["auth_token"]

    return None


def get_service_url(project_config: dict | None = None) -> str:
    """Resolve service URL: project config → env/default."""
    if project_config and project_config.get("service_url"):
        return project_config["service_url"].rstrip("/")
    return DEFAULT_SERVICE_URL
