"""
Configuration management for agent-trace.

Global config:  ~/.agent-trace/config.json   (stores auth_token)
Project config: .agent-trace/config.json     (stores storage, project_id, etc.)

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


# -------------------------------------------------------------------
# Load .env from the CLI tool's install directory (if present)
# -------------------------------------------------------------------

def _load_dotenv():
    """Read key=value pairs from the .env next to the installed lib."""
    # Installed layout: ~/.agent-trace/lib/agent_trace/config.py
    # .env lives at:    ~/.agent-trace/.env
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
            # Only set if not already in the environment (real env wins)
            os.environ.setdefault(key, value)
    except OSError:
        pass

_load_dotenv()


# -------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------

GLOBAL_CONFIG_DIR = Path.home() / ".agent-trace"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "config.json"

PROJECT_CONFIG_DIR_NAME = ".agent-trace"
PROJECT_CONFIG_FILE_NAME = "config.json"

# Default service URL — overridden by .env or AGENT_TRACE_URL env var
DEFAULT_SERVICE_URL = os.environ.get("AGENT_TRACE_URL", "http://localhost:5000").rstrip("/")


# -------------------------------------------------------------------
# Global config
# -------------------------------------------------------------------

def get_global_config() -> dict:
    """Load ~/.agent-trace/config.json (returns {} if missing)."""
    if GLOBAL_CONFIG_FILE.exists():
        try:
            return json.loads(GLOBAL_CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_global_config(config: dict) -> None:
    """Write ~/.agent-trace/config.json."""
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


# -------------------------------------------------------------------
# Project config
# -------------------------------------------------------------------

def _project_config_path(project_dir: str | None = None) -> Path:
    if project_dir is None:
        project_dir = os.getcwd()
    return Path(project_dir) / PROJECT_CONFIG_DIR_NAME / PROJECT_CONFIG_FILE_NAME


def get_project_config(project_dir: str | None = None) -> dict | None:
    """Load .agent-trace/config.json.  Returns None when not initialised."""
    path = _project_config_path(project_dir)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def save_project_config(config: dict, project_dir: str | None = None) -> None:
    """Write .agent-trace/config.json and update .gitignore."""
    if project_dir is None:
        project_dir = os.getcwd()

    config_dir = Path(project_dir) / PROJECT_CONFIG_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / PROJECT_CONFIG_FILE_NAME).write_text(
        json.dumps(config, indent=2) + "\n"
    )

    _ensure_gitignore(project_dir)


def _ensure_gitignore(project_dir: str) -> None:
    """Add .agent-trace/ to .gitignore if not already present."""
    gitignore = Path(project_dir) / ".gitignore"
    marker = ".agent-trace/"

    if gitignore.exists():
        content = gitignore.read_text()
        if marker not in content:
            with open(gitignore, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{marker}\n")
    else:
        gitignore.write_text(f"{marker}\n")


# -------------------------------------------------------------------
# Auth token resolution
# -------------------------------------------------------------------

def get_auth_token(project_config: dict | None = None) -> str | None:
    """
    Resolve the auth token.  Priority:
      1. AGENT_TRACE_TOKEN env var
      2. Global config  (~/.agent-trace/config.json)
      3. Project config (.agent-trace/config.json)
    """
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
    """
    Resolve the service URL.  Priority:
      1. AGENT_TRACE_URL env var / .env file  (already in DEFAULT_SERVICE_URL)
      2. Project config
      3. Default (http://localhost:5000)
    """
    if project_config and project_config.get("service_url"):
        return project_config["service_url"].rstrip("/")

    return DEFAULT_SERVICE_URL
