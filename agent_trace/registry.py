"""
Stable project identity registry (~/.agent-trace/projects.json).

Maps git repos to opaque project_id values using first commit, origin URL,
and canonical path — survives folder moves and re-clones.

POSIX: fcntl advisory lock during read-modify-write. Other platforms: best-effort.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
from typing import Any

from .storage import get_agent_trace_home, get_projects_registry_file


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


def _normalize_origin(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if u.endswith("/"):
        u = u[:-1]
    if u.endswith(".git"):
        u = u[:-4]
    return u.lower() if u.startswith("http") else u


def compute_project_identity(repo_root: str) -> dict[str, Any]:
    root = os.path.realpath(repo_root)
    fc = _first_commit_sha(root)
    origin = _origin_url(root)
    return {
        "first_commit_sha": fc,
        "origin_url": origin,
        "canonical_root": root,
    }


def _empty_registry() -> dict[str, Any]:
    return {
        "version": 1,
        "projects": {},
        "by_first_commit": {},
        "by_origin_url": {},
        "by_canonical_root": {},
    }


def _load_raw() -> dict[str, Any]:
    if not _projects_file().is_file():
        return _empty_registry()
    try:
        data = json.loads(_projects_file().read_text())
        if not isinstance(data, dict):
            return _empty_registry()
        data.setdefault("projects", {})
        data.setdefault("by_first_commit", {})
        data.setdefault("by_origin_url", {})
        data.setdefault("by_canonical_root", {})
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


def _new_project_id() -> str:
    return "at_" + secrets.token_hex(8)


def lookup_or_create_project_id(repo_root: str) -> str:
    """Return stable project_id for this git checkout (creates registry entry if needed)."""
    ident = compute_project_identity(repo_root)
    canon = ident["canonical_root"]
    fc = ident["first_commit_sha"]
    origin = ident["origin_url"]
    norm_origin = _normalize_origin(origin)

    with _RegistryLock():
        data = _load_raw()
        projects: dict[str, Any] = data["projects"]
        by_fc: dict[str, str] = data["by_first_commit"]
        by_o: dict[str, str] = data["by_origin_url"]
        by_root: dict[str, str] = data["by_canonical_root"]

        pid: str | None = by_root.get(canon)
        if not pid and fc and fc in by_fc:
            pid = by_fc[fc]
        if not pid and norm_origin and norm_origin in by_o:
            pid = by_o[norm_origin]

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if pid and pid in projects:
            rec = projects[pid]
            roots = rec.setdefault("known_roots", [])
            if canon not in roots:
                roots.append(canon)
            rec["canonical_root"] = canon
            if fc:
                rec["first_commit_sha"] = fc
            if origin:
                rec["origin_url"] = origin
            by_root[canon] = pid
            if fc:
                by_fc[fc] = pid
            if norm_origin:
                by_o[norm_origin] = pid
            _atomic_write(_projects_file(), json.dumps(data, indent=2) + "\n")
            return pid

        pid = _new_project_id()
        projects[pid] = {
            "first_commit_sha": fc,
            "origin_url": origin,
            "canonical_root": canon,
            "known_roots": [canon],
            "created_at": now,
        }
        by_root[canon] = pid
        if fc:
            by_fc[fc] = pid
        if norm_origin:
            by_o[norm_origin] = pid
        _atomic_write(_projects_file(), json.dumps(data, indent=2) + "\n")
        return pid


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
        data["by_canonical_root"][canon] = project_id
        _atomic_write(_projects_file(), json.dumps(data, indent=2) + "\n")


def list_projects() -> list[dict[str, Any]]:
    data = _load_raw()
    out: list[dict[str, Any]] = []
    for pid, rec in data.get("projects", {}).items():
        if isinstance(rec, dict):
            row = {"project_id": pid, **rec}
            out.append(row)
    out.sort(key=lambda x: x.get("created_at") or "")
    return out


def lookup_project_id_by_path(repo_root: str) -> str | None:
    """If repo_root is registered, return project_id; else None."""
    canon = os.path.realpath(repo_root)
    data = _load_raw()
    return data.get("by_canonical_root", {}).get(canon)
