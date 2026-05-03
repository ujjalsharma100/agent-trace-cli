"""
Project metadata registry (``<AGENT_TRACE_HOME>/projects.json``).

``project_id`` is now deterministic — it's the repo's canonical absolute path
with ``/`` replaced by ``-`` (see ``storage.path_to_project_id``). The registry
keeps per-project metadata (first commit sha, origin URL, known_roots) so
``agent-trace projects`` can show human context and so tests / tools can cross
reference past repo locations.

POSIX: fcntl advisory lock during read-modify-write. Other platforms: best-effort.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any

from .storage import (
    get_agent_trace_home,
    get_projects_registry_file,
    path_to_project_id,
)


def __getattr__(name: str):
    if name == "PROJECTS_FILE":
        return get_projects_registry_file()
    raise AttributeError(name)


def _projects_file() -> os.PathLike:
    """Resolve the projects registry file, honoring test-time patches."""
    import sys
    mod = sys.modules[__name__]
    if "PROJECTS_FILE" in mod.__dict__:
        return mod.__dict__["PROJECTS_FILE"]
    return get_projects_registry_file()


def _git(args: list[str], cwd: str, timeout: float = 15.0) -> str | None:
    import subprocess

    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _first_commit_sha(repo_root: str) -> str | None:
    out = _git(["log", "--reverse", "--format=%H", "--max-parents=0"], repo_root)
    if not out:
        return None
    line = out.split("\n", 1)[0].strip()
    return line or None


def _origin_url(repo_root: str) -> str | None:
    u = _git(["config", "--get", "remote.origin.url"], repo_root)
    return u or None


def compute_project_identity(repo_root: str) -> dict[str, Any]:
    root = os.path.realpath(repo_root)
    return {
        "first_commit_sha": _first_commit_sha(root),
        "origin_url": _origin_url(root),
        "canonical_root": root,
    }


def _empty_registry() -> dict[str, Any]:
    return {"version": 2, "projects": {}}


def _load_raw() -> dict[str, Any]:
    if not _projects_file().is_file():
        return _empty_registry()
    try:
        data = json.loads(_projects_file().read_text())
        if not isinstance(data, dict):
            return _empty_registry()
        data.setdefault("projects", {})
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_registry()


def _atomic_write(path: os.PathLike[str], text: str) -> None:
    p = os.fspath(path)
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".projects.", dir=d, text=True)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _RegistryLock:
    def __init__(self) -> None:
        self._fp: Any = None

    def __enter__(self) -> None:
        home = get_agent_trace_home()
        home.mkdir(parents=True, exist_ok=True)
        lock_path = home / ".registry.lock"
        self._fp = open(lock_path, "a+")
        if sys.platform != "win32":
            import fcntl

            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: object) -> None:
        if self._fp:
            if sys.platform != "win32":
                import fcntl

                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            self._fp.close()
            self._fp = None


def register_project_metadata(repo_root: str, project_id: str | None = None) -> str:
    """Upsert metadata for a repo. Returns the anchor-aware project_id.

    When ``project_id`` is not supplied, resolves it via the same
    anchor-aware path used everywhere else (``storage.resolve_project_id``)
    so the recorder, ``init``, ``blame``, and the registry all agree on
    the same id. Without this, a fresh ``.git/agent-trace-id`` anchor
    would be ignored and the registry would fall back to the path-derived
    id, leaving the recorder writing under one id while ``init`` wrote
    config under another.
    """
    canon = os.path.realpath(repo_root)
    if project_id is None:
        from .storage import resolve_project_id

        resolved = resolve_project_id(canon, create=True)
        pid = resolved or path_to_project_id(canon)
    else:
        pid = project_id
    fc = _first_commit_sha(canon)
    origin = _origin_url(canon)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with _RegistryLock():
        data = _load_raw()
        projects: dict[str, Any] = data["projects"]
        rec = projects.get(pid)
        if rec is None:
            rec = {
                "first_commit_sha": fc,
                "origin_url": origin,
                "canonical_root": canon,
                "known_roots": [canon],
                "created_at": now,
            }
            projects[pid] = rec
        else:
            roots = rec.setdefault("known_roots", [])
            if canon not in roots:
                roots.append(canon)
            rec["canonical_root"] = canon
            if fc:
                rec["first_commit_sha"] = fc
            if origin:
                rec["origin_url"] = origin
        _atomic_write(_projects_file(), json.dumps(data, indent=2) + "\n")
        return pid


def lookup_or_create_project_id(repo_root: str) -> str:
    """Return the deterministic project_id for a repo and record its metadata."""
    return register_project_metadata(repo_root)


def get_project_record(project_id: str) -> dict[str, Any] | None:
    data = _load_raw()
    rec = data["projects"].get(project_id)
    return dict(rec) if isinstance(rec, dict) else None


def register_known_root(project_id: str, root: str) -> None:
    canon = os.path.realpath(root)
    with _RegistryLock():
        data = _load_raw()
        projects: dict[str, Any] = data["projects"]
        if project_id not in projects:
            return
        rec = projects[project_id]
        roots = rec.setdefault("known_roots", [])
        if canon not in roots:
            roots.append(canon)
        _atomic_write(_projects_file(), json.dumps(data, indent=2) + "\n")


def list_projects() -> list[dict[str, Any]]:
    data = _load_raw()
    out: list[dict[str, Any]] = []
    for pid, rec in data.get("projects", {}).items():
        if isinstance(rec, dict):
            out.append({"project_id": pid, **rec})
    out.sort(key=lambda x: x.get("created_at") or "")
    return out


def lookup_project_id_by_path(repo_root: str) -> str | None:
    """Return the deterministic id for this path, regardless of registry state."""
    canon = os.path.realpath(repo_root)
    return path_to_project_id(canon)
