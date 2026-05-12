"""
Remote configuration management — git remote-like model.

Each project can have multiple named remotes. The remote URL has the shape
``<scheme>://<host>[:port]/<org_slug>/<project_slug>`` — mirroring
``github.com/<org>/<repo>``. The ``project_slug`` parsed out of the URL is
the **wire** ``project_id`` used by the sync routes; the local on-disk
data directory still uses the anchor-derived local id, so the
standalone-no-service flow is unaffected.

Tokens are stored separately:
  - ``global:<project_id>::<name>`` → ``~/.agent-trace/config.json`` under
    ``tokens.<project_id>::<name>``. Scoping by ``project_id`` keeps two
    projects that both call a remote ``origin`` from clobbering each
    other's token.
  - ``env:<VAR>``      → ``os.environ[VAR]`` at runtime (never persisted)
  - ``keychain:<name>``→ OS keychain (scaffold only — not yet implemented)

Legacy bare-name refs (``global:<name>``) written by older versions still
resolve — ``resolve_token`` treats whatever follows ``global:`` as a flat
key into ``tokens``.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .storage import ensure_project_dir, get_project_dir


# -------------------------------------------------------------------
# URL parsing
# -------------------------------------------------------------------

# Mirrors the server-side CHECK on orgs.slug and projects.project_id.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class RemoteUrlError(ValueError):
    """Raised when a remote URL doesn't match the slug-grammar."""


def parse_remote_url(url: str) -> tuple[str, str, str]:
    """Parse a remote URL into (base_url, org_slug, project_slug).

    Accepts: ``<scheme>://<host>[:port]/<org_slug>/<project_slug>`` with an
    optional trailing slash. Rejects bare-host URLs and URLs whose path
    component does not match the slug grammar.

    Raises ``RemoteUrlError`` with a hint pointing at the expected shape.
    """
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise RemoteUrlError(
            f"Unsupported scheme {parsed.scheme!r}. Expected http or https."
        )
    if not parsed.netloc:
        raise RemoteUrlError(f"URL is missing host: {url!r}")

    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        raise RemoteUrlError(
            "Remote URL must include the project path. "
            f"Expected ``<scheme>://<host>/<org_slug>/<project_slug>``; got {url!r}. "
            "Run `agent-trace project create <url>` if you need to register the slug first."
        )

    org_slug, project_slug = parts
    if not _SLUG_RE.match(org_slug):
        raise RemoteUrlError(
            f"Org slug {org_slug!r} must match {_SLUG_RE.pattern}."
        )
    if not _SLUG_RE.match(project_slug):
        raise RemoteUrlError(
            f"Project slug {project_slug!r} must match {_SLUG_RE.pattern}."
        )

    base = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return base, org_slug, project_slug


def is_slug(value: str) -> bool:
    return bool(_SLUG_RE.match(value or ""))


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

def _token_key(project_id: str, name: str) -> str:
    """Per-project key for global token storage.

    ``::`` separates the project_id from the remote name. project_ids are
    either opaque ``at-<hex>`` anchors or path-derived slugs — neither
    contains ``::`` — so the join is unambiguous.
    """
    return f"{project_id}::{name}"


def resolve_token(token_ref: str) -> str | None:
    """Resolve a token reference to an actual token value.

    Supported schemes:
      ``global:<key>``   — reads ``tokens.<key>`` from global config.
                           Modern refs use ``<project_id>::<name>``;
                           legacy bare-name keys still work.
      ``env:<VAR>``      — reads environment variable
      ``keychain:<name>``— (stub) returns None
      raw string         — returned as-is (direct token)
    """
    if token_ref.startswith("global:"):
        key = token_ref[7:]
        from .config import get_global_config
        tokens = get_global_config().get("tokens", {})
        return tokens.get(key)

    if token_ref.startswith("env:"):
        var = token_ref[4:]
        return os.environ.get(var)

    if token_ref.startswith("keychain:"):
        return None

    return token_ref


def _store_global_token(project_id: str, name: str, token: str) -> str:
    """Store a token under a per-project key. Returns the storage key so
    the caller can write the matching ``token_ref``.
    """
    from .config import get_global_config, save_global_config
    key = _token_key(project_id, name)
    cfg = get_global_config()
    tokens = cfg.setdefault("tokens", {})
    tokens[key] = token
    save_global_config(cfg)
    return key


def _delete_global_token(project_id: str, name: str) -> None:
    """Remove the per-project token entry from global config, if present."""
    from .config import get_global_config, save_global_config
    key = _token_key(project_id, name)
    cfg = get_global_config()
    tokens = cfg.get("tokens") or {}
    if key in tokens:
        del tokens[key]
        cfg["tokens"] = tokens
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
    """Add a new named remote.  Returns the remote config dict.

    The URL must include the ``<org>/<project>`` path; ``parse_remote_url``
    raises ``RemoteUrlError`` otherwise.
    """
    remotes = _load_remotes(project_id)
    if name in remotes:
        raise ValueError(f"Remote '{name}' already exists. Use set-url to change its URL.")

    base_url, org_slug, project_slug = parse_remote_url(url)

    auth: dict[str, str] | None = None
    if token:
        key = _store_global_token(project_id, name, token)
        auth = {"type": "bearer", "token_ref": f"global:{key}"}
    elif token_env:
        auth = {"type": "bearer", "token_ref": f"env:{token_env}"}
    elif token_keychain:
        auth = {"type": "bearer", "token_ref": f"keychain:{token_keychain}"}

    entry: dict[str, Any] = {
        "url": url.rstrip("/"),
        "base_url": base_url,
        "org_slug": org_slug,
        "project_slug": project_slug,
    }
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
    """Remove a named remote.  Returns True if it existed.

    Also drops the per-project token entry from the global config so a
    deleted remote doesn't leave its secret behind.
    """
    remotes = _load_remotes(project_id)
    if name not in remotes:
        return False
    del remotes[name]
    _save_remotes(project_id, remotes)
    _delete_global_token(project_id, name)
    return True


def set_remote_url(project_id: str, name: str, url: str) -> None:
    """Change the URL for an existing remote. Re-parses to refresh the
    derived slug fields.
    """
    remotes = _load_remotes(project_id)
    if name not in remotes:
        raise ValueError(f"Remote '{name}' does not exist.")
    base_url, org_slug, project_slug = parse_remote_url(url)
    remotes[name].update({
        "url": url.rstrip("/"),
        "base_url": base_url,
        "org_slug": org_slug,
        "project_slug": project_slug,
    })
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
        key = _store_global_token(project_id, name, token)
        remotes[name]["auth"] = {"type": "bearer", "token_ref": f"global:{key}"}
    elif token_env:
        remotes[name]["auth"] = {"type": "bearer", "token_ref": f"env:{token_env}"}
    else:
        raise ValueError("Provide --token or --token-env.")

    _save_remotes(project_id, remotes)


def rename_remote(project_id: str, old_name: str, new_name: str) -> None:
    """Rename a remote.

    If the remote's token lives in the global config under the per-project
    scoped key, migrate it to match the new name and rewrite ``token_ref``.
    """
    remotes = _load_remotes(project_id)
    if old_name not in remotes:
        raise ValueError(f"Remote '{old_name}' does not exist.")
    if new_name in remotes:
        raise ValueError(f"Remote '{new_name}' already exists.")

    entry = remotes.pop(old_name)
    auth = entry.get("auth") or {}
    old_ref = auth.get("token_ref", "")
    old_key = _token_key(project_id, old_name)
    if old_ref == f"global:{old_key}":
        from .config import get_global_config, save_global_config
        cfg = get_global_config()
        tokens = cfg.setdefault("tokens", {})
        if old_key in tokens:
            new_key = _token_key(project_id, new_name)
            tokens[new_key] = tokens.pop(old_key)
            save_global_config(cfg)
            entry["auth"] = {**auth, "token_ref": f"global:{new_key}"}

    remotes[new_name] = entry
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
    """Full user-facing URL (including ``/<org>/<project>``)."""
    return remote_conf.get("url", "")


def get_remote_base_url(remote_conf: dict[str, Any]) -> str:
    """Service base URL (``<scheme>://<host>``) — used for API calls.

    Falls back to re-parsing ``url`` for older remotes written before the
    derived fields existed.
    """
    base = remote_conf.get("base_url")
    if base:
        return base
    url = remote_conf.get("url", "")
    if not url:
        return ""
    try:
        base, _, _ = parse_remote_url(url)
        return base
    except RemoteUrlError:
        return url


def get_remote_project_slug(remote_conf: dict[str, Any]) -> str | None:
    """Wire ``project_id`` for sync calls. ``None`` only for malformed remotes."""
    slug = remote_conf.get("project_slug")
    if slug:
        return slug
    url = remote_conf.get("url", "")
    if not url:
        return None
    try:
        _, _, slug = parse_remote_url(url)
        return slug
    except RemoteUrlError:
        return None


def get_remote_org_slug(remote_conf: dict[str, Any]) -> str | None:
    slug = remote_conf.get("org_slug")
    if slug:
        return slug
    url = remote_conf.get("url", "")
    if not url:
        return None
    try:
        _, slug, _ = parse_remote_url(url)
        return slug
    except RemoteUrlError:
        return None


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
        "base_url": get_remote_base_url(conf),
        "org_slug": get_remote_org_slug(conf) or "",
        "project_slug": get_remote_project_slug(conf) or "",
        "auth_type": auth.get("type", "none"),
        "token_ref": token_ref,
        "token_masked": _mask_token(resolved),
    }


# -------------------------------------------------------------------
# Network: token introspection (whoami) + scope guards
# -------------------------------------------------------------------

class TokenScopeError(Exception):
    """A token's resolved scope doesn't match the URL it's being used against.

    Carries the structured ``code`` so CLI handlers (project create, remote
    add, push/pull, doctor) can render a uniform message and the user can
    grep for a stable identifier.

    Codes:
      - ``org_slug_mismatch``     — token org differs from URL ``<org_slug>``.
      - ``project_scope_mismatch`` — project-scoped token used with a URL
        whose ``<project_slug>`` is a different project.
      - ``whoami_unsupported``    — the service is too old to expose
        ``GET /api/v1/auth/whoami``; the caller decides whether to soft-fail.
      - ``whoami_unauthorized``   — token rejected (401).
      - ``whoami_failed``         — any other reason whoami didn't answer.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class WhoamiUnsupportedError(TokenScopeError):
    """Service did not implement ``GET /api/v1/auth/whoami`` (404)."""

    def __init__(self) -> None:
        super().__init__(
            "whoami_unsupported",
            "Remote service does not implement GET /api/v1/auth/whoami "
            "(upgrade the service to enable strict org/project scope checks).",
        )


def whoami(base_url: str, token: str, *, timeout: int = 15) -> dict[str, Any]:
    """Fetch the resolved scope of ``token`` from ``base_url``.

    Returns a dict with ``org_id``, ``org_slug``, ``project_id_scope``,
    ``scopes``. Raises ``WhoamiUnsupportedError`` for 404 (older service)
    and ``TokenScopeError`` for any other failure (401, 5xx, network).
    """
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur

    req = _ur.Request(
        f"{base_url.rstrip('/')}/api/v1/auth/whoami",
        method="GET",
    )
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with _ur.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except _ue.HTTPError as e:
        if e.code == 404:
            raise WhoamiUnsupportedError() from None
        if e.code == 401:
            raise TokenScopeError(
                "whoami_unauthorized",
                "Token rejected by remote service (401). The token may be "
                "invalid, revoked, or not minted by this service.",
            ) from None
        raise TokenScopeError(
            "whoami_failed",
            f"GET /api/v1/auth/whoami returned HTTP {e.code}.",
        ) from None
    except (_ue.URLError, OSError, TimeoutError) as e:
        raise TokenScopeError(
            "whoami_failed",
            f"Could not reach {base_url}/api/v1/auth/whoami: {e}",
        ) from None


def assert_token_matches_url(
    base_url: str,
    token: str,
    *,
    expected_org_slug: str,
    expected_project_slug: str | None = None,
    allow_unsupported: bool = True,
) -> dict[str, Any]:
    """Verify the token's scope matches the URL the user typed.

    - ``expected_org_slug`` MUST equal the slug of the token's org.
    - When the token is project-scoped (``project_id_scope`` set), and
      ``expected_project_slug`` is provided, they must be equal.

    Returns the whoami payload on success. Raises ``TokenScopeError`` on
    mismatch. If the remote service is too old to support ``whoami`` and
    ``allow_unsupported`` is True, returns the unsupported error in the
    payload (caller decides to warn vs. fail).
    """
    try:
        info = whoami(base_url, token)
    except WhoamiUnsupportedError:
        if allow_unsupported:
            return {"_unsupported": True}
        raise

    actual_org = info.get("org_slug")
    if actual_org and actual_org != expected_org_slug:
        raise TokenScopeError(
            "org_slug_mismatch",
            f"Token belongs to org {actual_org!r} but the URL targets "
            f"org {expected_org_slug!r}. Use a token for {expected_org_slug!r} "
            f"or change the URL's org segment to {actual_org!r}.",
        )

    project_scope = info.get("project_id_scope")
    if (
        project_scope
        and expected_project_slug
        and project_scope != expected_project_slug
    ):
        raise TokenScopeError(
            "project_scope_mismatch",
            f"Token is scoped to project {project_scope!r} but the URL targets "
            f"project {expected_project_slug!r}. Use an org-scoped token, a "
            f"token scoped to {expected_project_slug!r}, or change the URL.",
        )

    return info


# -------------------------------------------------------------------
# Network: project registration
# -------------------------------------------------------------------

class ProjectRegistrationError(Exception):
    """``register_project_via_remote`` raised; carries the HTTP status."""

    def __init__(self, status: int, message: str, code: str | None = None) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


def register_project_via_remote(
    base_url: str,
    project_slug: str,
    *,
    org_slug: str | None = None,
    token: str | None = None,
    admin_secret: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """POST /api/v1/projects on the service. Returns the project record on
    success.

    Auth: pass ``token`` for an org-scoped Bearer (must carry
    ``projects:write``), or ``admin_secret`` for the X-Admin-Secret path.

    ``org_slug`` is sent in the body so the server cross-checks it against the
    caller's org. The CLI always derives this from the URL's first path
    segment so a URL like ``https://svc/foo-org/myproj`` cannot end up writing
    rows under a *different* org just because the token happened to be valid.
    """
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur

    body: dict[str, Any] = {"project_id": project_slug}
    if org_slug is not None:
        body["org_slug"] = org_slug
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description

    req = _ur.Request(
        f"{base_url.rstrip('/')}/api/v1/projects",
        data=_json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    if admin_secret:
        req.add_header("X-Admin-Secret", admin_secret)
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with _ur.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode())
    except _ue.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        try:
            payload = _json.loads(raw) if raw else {}
        except _json.JSONDecodeError:
            payload = {"raw": raw}
        msg = payload.get("error") or f"HTTP {e.code}"
        raise ProjectRegistrationError(e.code, msg, payload.get("code")) from None
