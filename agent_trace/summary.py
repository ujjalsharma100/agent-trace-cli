"""
Pluggable session summaries — external command reads JSON on stdin, writes JSON on stdout.

Opt-in via ``project-config.json`` → ``summary.enabled`` and ``summary.command``.
Storage: ``session-summaries.jsonl`` under the project dir. Failures never raise through hooks.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from .models import Trace
from .storage import (
    ensure_project_dir,
    get_session_summaries_path,
    get_traces_path,
    resolve_project_id,
)


def _log(msg: str) -> None:
    print(f"agent-trace: {msg}", file=sys.stderr)


def load_traces_for_session(project_dir: str, session_id: str) -> list[Trace]:
    """All traces in the project whose metadata matches ``session_id``."""
    if not session_id:
        return []
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return []
    path = get_traces_path(pid)
    if not path.exists():
        return []
    out: list[Trace] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            sid = meta.get("session_id") or meta.get("conversation_id")
            if sid and str(sid) == str(session_id):
                try:
                    out.append(Trace.from_dict(row))
                except Exception:
                    continue
    except OSError:
        pass
    return out


def _load_traces_by_ids(project_id: str, want_ids: set[str]) -> list[Trace]:
    if not want_ids:
        return []
    path = get_traces_path(project_id)
    if not path.exists():
        return []
    out: list[Trace] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = row.get("id")
            if tid is not None and str(tid) in want_ids:
                try:
                    out.append(Trace.from_dict(row))
                except Exception:
                    continue
    except OSError:
        pass
    return out


def _ledger_for_commit(project_id: str, commit_sha: str) -> dict[str, Any] | None:
    from .storage import get_ledgers_path

    path = get_ledgers_path(project_id)
    if not path.exists():
        return None
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                led = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(led.get("commit_sha", "")) == commit_sha:
                return led if isinstance(led, dict) else None
    except OSError:
        pass
    return None


def generate_summary(
    session_traces: list[Trace],
    command: str,
    timeout_seconds: int = 30,
) -> dict[str, str] | None:
    """Run the configured command with a JSON payload on stdin.

    The process must print a single JSON object mapping file paths to summary strings.
    Returns ``None`` on timeout, non-zero exit, or invalid JSON.
    """
    if not command.strip() or not session_traces:
        return None
    payload = {
        "version": 1,
        "traces": [t.to_dict() for t in session_traces],
    }
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None
    if not argv:
        return None
    try:
        r = subprocess.run(
            argv,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None
    except (OSError, ValueError):
        return None
    if r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    result: dict[str, str] = {}
    for k, v in parsed.items():
        if isinstance(k, str) and isinstance(v, str):
            result[k] = v
    return result if result else None


def append_summary(project_id: str, session_id: str, summaries: dict[str, Any]) -> None:
    """Append one line to ``session-summaries.jsonl`` (atomic append)."""
    if not session_id:
        return
    ensure_project_dir(project_id)
    path = get_session_summaries_path(project_id)
    row = {
        "session_id": session_id,
        "summaries": dict(summaries),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _latest_summaries_by_session(project_id: str) -> dict[str, dict[str, str]]:
    """For each ``session_id``, the summaries from the last JSONL line mentioning it."""
    path = get_session_summaries_path(project_id)
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = row.get("session_id")
            sums = row.get("summaries")
            if not sid or not isinstance(sums, dict):
                continue
            clean: dict[str, str] = {}
            for k, v in sums.items():
                if isinstance(k, str) and isinstance(v, str):
                    clean[k] = v
            if clean:
                out[str(sid)] = clean
    except OSError:
        pass
    return out


def get_summary_for_commit(project_id: str, commit_sha: str) -> dict[str, str] | None:
    """Merge per-file summaries from all sessions that contributed traces to this commit."""
    led = _ledger_for_commit(project_id, commit_sha)
    if not led:
        return None
    tid_list = [str(x) for x in led.get("trace_ids", [])]
    if not tid_list:
        return None
    want = set(tid_list)
    traces = _load_traces_by_ids(project_id, want)
    sessions: set[str] = set()
    for tr in traces:
        meta = tr.metadata or {}
        sid = meta.get("session_id") or meta.get("conversation_id")
        if sid:
            sessions.add(str(sid))
    if not sessions:
        return None
    by_sess = _latest_summaries_by_session(project_id)
    merged: dict[str, str] = {}
    for sid in sorted(sessions):
        block = by_sess.get(sid)
        if block:
            merged.update(block)
    return merged if merged else None


def merge_note_summaries(
    project_dir: str,
    ledger: dict[str, Any],
    static_summaries: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Optional static ``notes.summaries`` plus session-generated summaries (session wins)."""
    pid = resolve_project_id(project_dir, create=False)
    out: dict[str, str] = {}
    if isinstance(static_summaries, dict):
        for k, v in static_summaries.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k] = v
    if pid:
        sha = str(ledger.get("commit_sha", ""))
        if sha:
            dyn = get_summary_for_commit(pid, sha)
            if dyn:
                out.update(dyn)
    return out if out else None


def run_session_summary_hook(data: dict[str, Any]) -> None:
    """Called from ``record`` on session-end events; never raises."""
    try:
        from .config import get_project_config

        cwd = data.get("cwd") or os.getcwd()
        session_id = str(
            data.get("conversation_id")
            or data.get("session_id")
            or "",
        ).strip()
        if not session_id:
            return
        cfg = get_project_config(project_dir=cwd)
        if not cfg:
            return
        sm = cfg.get("summary")
        if not isinstance(sm, dict) or not sm.get("enabled"):
            return
        command = sm.get("command")
        if not command or not isinstance(command, str):
            return
        timeout = int(sm.get("timeout_seconds", 30))

        pid = resolve_project_id(cwd, create=False)
        if not pid:
            return
        traces = load_traces_for_session(cwd, session_id)
        if not traces:
            return
        summaries = generate_summary(traces, command, timeout_seconds=timeout)
        if summaries:
            append_summary(pid, session_id, summaries)
        else:
            _log("summary command failed or produced no valid JSON output")
    except Exception as exc:
        _log(f"summary hook error: {exc}")


def run_summary_generate(project_dir: str, session_id: str) -> dict[str, str] | None:
    """CLI/manual regeneration of summaries for a session."""
    from .config import get_project_config

    cfg = get_project_config(project_dir=project_dir)
    if not cfg:
        return None
    sm = cfg.get("summary")
    if not isinstance(sm, dict) or not sm.get("enabled"):
        return None
    command = sm.get("command")
    if not command or not isinstance(command, str):
        return None
    timeout = int(sm.get("timeout_seconds", 30))
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None
    traces = load_traces_for_session(project_dir, session_id)
    if not traces:
        return None
    summaries = generate_summary(traces, command, timeout_seconds=timeout)
    if summaries:
        append_summary(pid, session_id, summaries)
    return summaries
