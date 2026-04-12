"""
Remote configuration management — git remote-like model.

Each project can have multiple named remotes, each with a URL and
separately-stored auth.  Token storage backends:

  - ``global:<name>``  → ``~/.agent-trace/config.json`` under ``tokens.<name>``
  - ``env:<VAR>``      → ``os.environ[VAR]`` at runtime (never persisted)
  - ``keychain:<name>``→ OS keychain (scaffold only — not yet implemented)

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .storage import ensure_project_dir, get_project_dir


# -------------------------------------------------------------------
# File helpers
# -------------------------------------------------------------------

def _remotes_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "remotes.json"


def _load_remotes(project_id: str) -> dict[str, Any]:
    p = _remotes_path(project_id)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_remotes(project_id: str, remotes: dict[str, Any]) -> None:
    ensure_project_dir(project_id)
    p = _remotes_path(project_id)
    p.write_text(json.dumps(remotes, indent=2) + "\n")
    try:
        p.chmod(0o600)
    except OSError:
        pass


# -------------------------------------------------------------------
# Token resolution
# -------------------------------------------------------------------

def resolve_token(token_ref: str) -> str | None:
    """Resolve a token reference to an actual token value.

    Supported schemes:
      ``global:<name>``  — reads ``tokens.<name>`` from global config
      ``env:<VAR>``      — reads environment variable
      ``keychain:<name>``— (stub) returns None
      raw string         — returned as-is (direct token)
    """
    if token_ref.startswith("global:"):
        name = token_ref[7:]
        from .config import get_global_config
        tokens = get_global_config().get("tokens", {})
        return tokens.get(name)

    if token_ref.startswith("env:"):
        var = token_ref[4:]
        return os.environ.get(var)

    if token_ref.startswith("keychain:"):
        return None

    return token_ref


def _store_global_token(name: str, token: str) -> None:
    """Store a token under ``tokens.<name>`` in the global config."""
    from .config import get_global_config, save_global_config
    cfg = get_global_config()
    tokens = cfg.setdefault("tokens", {})
    tokens[name] = token
    save_global_config(cfg)


def _mask_token(token: str | None) -> str:
    """Mask a token for safe display."""
    if not token:
        return "(unresolved)"
    if len(token) <= 8:
        return "****"
    return f"{'*' * 8}...{token[-4:]}"


# -------------------------------------------------------------------
# CRUD operations
# -------------------------------------------------------------------

def add_remote(
    project_id: str,
    name: str,
    url: str,
    *,
    token: str | None = None,
    token_env: str | None = None,
    token_keychain: str | None = None,
) -> dict[str, Any]:
    """Add a new named remote.  Returns the remote config dict."""
    remotes = _load_remotes(project_id)
    if name in remotes:
        raise ValueError(f"Remote '{name}' already exists. Use set-url to change its URL.")

    auth: dict[str, str] | None = None
    if token:
        _store_global_token(name, token)
        auth = {"type": "bearer", "token_ref": f"global:{name}"}
    elif token_env:
        auth = {"type": "bearer", "token_ref": f"env:{token_env}"}
    elif token_keychain:
        auth = {"type": "bearer", "token_ref": f"keychain:{token_keychain}"}

    entry: dict[str, Any] = {"url": url.rstrip("/")}
    if auth:
        entry["auth"] = auth

    remotes[name] = entry
    _save_remotes(project_id, remotes)
    return entry


def get_remote(project_id: str, name: str) -> dict[str, Any] | None:
    """Return config for a single remote, or None."""
    return _load_remotes(project_id).get(name)


def list_remotes(project_id: str) -> list[dict[str, Any]]:
    """Return list of ``{name, url, token_ref}`` dicts."""
    remotes = _load_remotes(project_id)
    result = []
    for rname, rconf in remotes.items():
        auth = rconf.get("auth") or {}
        result.append({
            "name": rname,
            "url": rconf.get("url", ""),
            "token_ref": auth.get("token_ref", ""),
        })
    return result


def remove_remote(project_id: str, name: str) -> bool:
    """Remove a named remote.  Returns True if it existed."""
    remotes = _load_remotes(project_id)
    if name not in remotes:
        return False
    del remotes[name]
    _save_remotes(project_id, remotes)
    return True


def set_remote_url(project_id: str, name: str, url: str) -> None:
    """Change the URL for an existing remote."""
    remotes = _load_remotes(project_id)
    if name not in remotes:
        raise ValueError(f"Remote '{name}' does not exist.")
    remotes[name]["url"] = url.rstrip("/")
    _save_remotes(project_id, remotes)


def set_remote_token(
    project_id: str,
    name: str,
    *,
    token: str | None = None,
    token_env: str | None = None,
) -> None:
    """Update the auth token for an existing remote."""
    remotes = _load_remotes(project_id)
    if name not in remotes:
        raise ValueError(f"Remote '{name}' does not exist.")

    if token:
        _store_global_token(name, token)
        remotes[name]["auth"] = {"type": "bearer", "token_ref": f"global:{name}"}
    elif token_env:
        remotes[name]["auth"] = {"type": "bearer", "token_ref": f"env:{token_env}"}
    else:
        raise ValueError("Provide --token or --token-env.")

    _save_remotes(project_id, remotes)


def rename_remote(project_id: str, old_name: str, new_name: str) -> None:
    """Rename a remote."""
    remotes = _load_remotes(project_id)
    if old_name not in remotes:
        raise ValueError(f"Remote '{old_name}' does not exist.")
    if new_name in remotes:
        raise ValueError(f"Remote '{new_name}' already exists.")
    remotes[new_name] = remotes.pop(old_name)
    _save_remotes(project_id, remotes)


def get_default_remote(project_id: str) -> str | None:
    """Return the default remote name.

    Rules: if only one remote, it's the default.
    If multiple, look for ``default`` key in project config.
    """
    remotes = _load_remotes(project_id)
    if not remotes:
        return None
    if len(remotes) == 1:
        return next(iter(remotes))

    from .config import get_project_config
    from .storage import resolve_project_id
    cfg = get_project_config() or {}
    default = cfg.get("remote", {}).get("default")
    if default and default in remotes:
        return default
    if "origin" in remotes:
        return "origin"
    return None


def set_default_remote(project_id: str, name: str) -> None:
    """Set the default remote in project config."""
    remotes = _load_remotes(project_id)
    if name not in remotes:
        raise ValueError(f"Remote '{name}' does not exist.")
    from .config import get_project_config, save_project_config
    cfg = get_project_config() or {}
    cfg.setdefault("remote", {})["default"] = name
    save_project_config(cfg)


def resolve_remote(project_id: str, name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Resolve a remote by name (or default).  Returns ``(name, config)``."""
    if name is None:
        name = get_default_remote(project_id)
    if name is None:
        raise ValueError(
            "No remote configured. Run 'agent-trace remote add <name> <url>' first."
        )
    conf = get_remote(project_id, name)
    if conf is None:
        raise ValueError(f"Remote '{name}' not found.")
    return name, conf


def get_remote_url(remote_conf: dict[str, Any]) -> str:
    """Extract URL from a remote config dict."""
    return remote_conf.get("url", "")


def get_remote_token(remote_conf: dict[str, Any]) -> str | None:
    """Resolve the auth token for a remote config dict."""
    auth = remote_conf.get("auth")
    if not auth:
        return None
    ref = auth.get("token_ref", "")
    return resolve_token(ref)


def show_remote(project_id: str, name: str) -> dict[str, Any] | None:
    """Return a display-safe view of a remote (token masked)."""
    conf = get_remote(project_id, name)
    if conf is None:
        return None
    auth = conf.get("auth") or {}
    token_ref = auth.get("token_ref", "")
    resolved = resolve_token(token_ref) if token_ref else None
    return {
        "name": name,
        "url": conf.get("url", ""),
        "auth_type": auth.get("type", "none"),
        "token_ref": token_ref,
        "token_masked": _mask_token(resolved),
    }
