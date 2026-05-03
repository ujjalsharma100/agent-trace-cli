"""
Per-session manifest (~/.agent-trace/sessions/<session_id>.json) — project
touch counts for cross-repo sessions (Phase 1b).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any

from .storage import get_sessions_dir


def _sessions_dir():
    return get_sessions_dir()


class _SessionLock:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._fp: Any = None

    def __enter__(self) -> None:
        _sessions_dir().mkdir(parents=True, exist_ok=True)
        path = _sessions_dir() / f".{self.session_id}.lock"
        self._fp = open(path, "a+")
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


def _atomic_write(path: os.PathLike[str], text: str) -> None:
    p = os.fspath(path)
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sess.", dir=d, text=True)
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


def primary_project_id_for_session(session_id: str) -> str | None:
    """Project id that received the most trace touches this session (nested-repo safe).

    Used so summary hooks attribute to the same project as file-based traces when
    the IDE workspace cwd is a parent folder but edits targeted an inner git repo.
    """
    if not session_id:
        return None
    path = _sessions_dir() / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    counts = data.get("edit_counts") if isinstance(data.get("edit_counts"), dict) else {}
    if counts:
        best: str | None = None
        best_n = -1
        for pid, n in counts.items():
            if not isinstance(pid, str) or not pid:
                continue
            try:
                v = int(n)
            except (TypeError, ValueError):
                continue
            if v > best_n:
                best_n = v
                best = pid
        if best is not None:
            return best
    projects = data.get("projects") if isinstance(data.get("projects"), list) else []
    if len(projects) == 1 and isinstance(projects[0], str) and projects[0]:
        return projects[0]
    return None


def touch_session_project(
    session_id: str,
    project_id: str,
    *,
    tool_name: str | None = None,
    transcript_path: str | None = None,
) -> None:
    """Record that this session touched a project; bump edit counter."""
    if not session_id or not project_id:
        return
    path = _sessions_dir() / f"{session_id}.json"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _SessionLock(session_id):
        data: dict[str, Any]
        if path.is_file():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}
        data.setdefault("session_id", session_id)
        data.setdefault("started_at", now)
        data["ended_at"] = None
        if tool_name:
            data.setdefault("tool", {})["name"] = tool_name
        if transcript_path:
            data["transcript_path"] = transcript_path
        projects: list[str] = list(data.get("projects") or [])
        if project_id not in projects:
            projects.append(project_id)
        data["projects"] = projects
        counts: dict[str, int] = dict(data.get("edit_counts") or {})
        counts[project_id] = int(counts.get(project_id, 0)) + 1
        data["edit_counts"] = counts
        _atomic_write(path, json.dumps(data, indent=2) + "\n")
