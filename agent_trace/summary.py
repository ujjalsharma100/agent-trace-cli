"""
Conversation summaries — pluggable summarization of transcripts referenced
by traces, keyed by ``conversation_id``.

For each session the transcript bytes live in the per-project
content-addressed cache (``<project>/conversations/<sha[:2]>/<sha>``).
Summary generation reads the latest cached snapshot referenced by traces
for a given conversation_id and pipes it to a user-configured command.
The command's stdout is stored keyed by ``conversation_id``.

Opt-in via ``project-config.json`` → ``summary.enabled`` and
``summary.command``. Storage: ``session-summaries.jsonl`` under the
project dir, one row per ``(conversation_id, summary)``. Failures never
raise through hooks.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from .conversations import (
    compute_conversation_id,
    latest_sha_for_conversation,
    read_blob_from_cache,
)
from .storage import (
    ensure_project_dir,
    get_session_summaries_path,
    get_traces_path,
    resolve_project_id,
)
from .summary_presets import augment_path_env


def _log(msg: str) -> None:
    print(f"agent-trace: {msg}", file=sys.stderr)


# -------------------------------------------------------------------
# Conversation id → latest content_sha256 lookup (from local traces)
# -------------------------------------------------------------------

def _read_transcript_from_cache(
    project_id: str, conversation_id: str,
) -> str | None:
    """Read the latest cached transcript bytes for a conversation_id."""
    sha = latest_sha_for_conversation(project_id, conversation_id)
    if not sha:
        return None
    data = read_blob_from_cache(project_id, sha)
    if data is None:
        return None
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


# -------------------------------------------------------------------
# Summary command runner
# -------------------------------------------------------------------

def generate_summary_text(
    transcript_text: str,
    command: str,
    timeout_seconds: int = 30,
) -> str | None:
    """Run ``command`` with ``transcript_text`` on stdin; return trimmed stdout or ``None``."""
    if not command.strip() or not transcript_text:
        return None
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None
    if not argv:
        return None
    try:
        r = subprocess.run(
            argv,
            input=transcript_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=augment_path_env(),
        )
    except subprocess.TimeoutExpired:
        _log(
            f"summary command timed out after {timeout_seconds}s "
            "(try: agent-trace config set summary.timeout-seconds 120)",
        )
        return None
    except (OSError, ValueError) as e:
        _log(f"summary command failed to start: {e}")
        return None
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if err:
            _log(f"summary command stderr (exit {r.returncode}): {err[:1200]}")
        else:
            _log(f"summary command exited with {r.returncode}")
        return None
    out = (r.stdout or "").strip()
    if not out:
        err = (r.stderr or "").strip()
        if err:
            _log(f"summary command produced empty stdout; stderr: {err[:1200]}")
        return None
    return out


# -------------------------------------------------------------------
# session-summaries.jsonl I/O
# -------------------------------------------------------------------

def append_summary(
    project_id: str,
    conversation_id: str,
    summary: str,
    *,
    session_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any] | None:
    """Append one row to ``session-summaries.jsonl``: ``{conversation_id, summary, ...}``.

    Returns the appended row dict (used by sync materialisation to track the
    synthetic ``<conversation_id>:<created_at>`` key) or ``None`` on error.
    """
    if not conversation_id or not summary:
        return None
    ensure_project_dir(project_id)
    path = get_session_summaries_path(project_id)
    row: dict[str, Any] = {
        "conversation_id": conversation_id,
        "summary": summary,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    if session_id:
        row["session_id"] = session_id
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return row


def iter_summary_rows(project_id: str) -> list[dict[str, Any]]:
    """All summary rows for a project, in file order. Used by sync push."""
    path = get_session_summaries_path(project_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(row, dict)
                and isinstance(row.get("conversation_id"), str)
                and isinstance(row.get("summary"), str)
            ):
                out.append(row)
    except OSError:
        pass
    return out


def latest_summary_by_id(project_id: str) -> dict[str, str]:
    """Map ``conversation_id → latest summary`` across all rows (file order)."""
    out: dict[str, str] = {}
    for row in iter_summary_rows(project_id):
        cid = row.get("conversation_id")
        s = row.get("summary")
        if isinstance(cid, str) and isinstance(s, str) and cid and s:
            out[cid] = s
    return out


def get_summary_for_id(project_dir: str, conversation_id: str) -> str | None:
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None
    return latest_summary_by_id(pid).get(conversation_id)


# -------------------------------------------------------------------
# Per-ledger / per-commit summary lookup (used by git notes)
# -------------------------------------------------------------------

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


def _ids_in_ledger(ledger: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    files = ledger.get("files", {})
    if not isinstance(files, dict):
        return ids
    for fl in files.values():
        if not isinstance(fl, dict):
            continue
        for seg in fl.get("line_attributions", []):
            if not isinstance(seg, dict):
                continue
            cid = seg.get("conversation_id")
            if isinstance(cid, str) and cid:
                ids.add(cid)
    return ids


def get_summary_for_commit(project_id: str, commit_sha: str) -> dict[str, str] | None:
    """``{conversation_id: summary}`` for every id referenced by the commit's ledger."""
    led = _ledger_for_commit(project_id, commit_sha)
    if not led:
        return None
    ids = _ids_in_ledger(led)
    if not ids:
        return None
    by_id = latest_summary_by_id(project_id)
    out = {c: by_id[c] for c in ids if c in by_id}
    return out if out else None


def merge_note_summaries(
    project_dir: str,
    ledger: dict[str, Any],
    static_summaries: dict[str, Any] | None = None,  # accepted for caller compat; ignored
) -> dict[str, str] | None:
    """Resolve conversation_id → summary for the git note. Static map is ignored."""
    del static_summaries
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None
    sha = str(ledger.get("commit_sha", ""))
    if not sha:
        return None
    return get_summary_for_commit(pid, sha)


def all_session_conversations_for_ledger(
    project_dir: str,
    ledger: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Build ``all_session_conversations`` for a git note: every distinct
    ``conversation_id`` from traces in the same staging window as the ledger,
    with the latest stored summary per id (if any).
    """
    from .ledger import list_traces_in_staging_window

    parent_sha = ledger.get("parent_sha")
    parent_at = ledger.get("parent_committed_at")
    committed_at = ledger.get("committed_at")

    parents: list[tuple[str, str | None]] = []
    if parent_sha:
        parents.append((str(parent_sha), str(parent_at) if parent_at else None))
    raw = list_traces_in_staging_window(
        project_dir,
        parents,
        str(committed_at) if committed_at else None,
    )
    ids: list[str] = []
    seen: set[str] = set()
    for t in raw:
        for fe in t.get("files") or []:
            if not isinstance(fe, dict):
                continue
            for conv in fe.get("conversations") or []:
                if not isinstance(conv, dict):
                    continue
                cid = conv.get("id")
                if isinstance(cid, str) and cid and cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
    if not ids:
        return None
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return [{"conversation_id": c, "summary": None} for c in ids]
    by_id = latest_summary_by_id(pid)
    out: list[dict[str, Any]] = []
    for cid in ids:
        s = by_id.get(cid)
        row: dict[str, Any] = {"conversation_id": cid}
        row["summary"] = s if s is not None else None
        out.append(row)
    return out


# -------------------------------------------------------------------
# Session-id → conversation_ids helper (for `summary generate --session-id`)
# -------------------------------------------------------------------

def _conversation_ids_for_session(project_id: str, session_id: str) -> list[str]:
    """All distinct ``conversation_id``s referenced by traces in this session, in first-seen order."""
    path = get_traces_path(project_id)
    if not path.exists():
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = row.get("metadata") or {}
            sid = meta.get("session_id") or meta.get("conversation_id")
            if not sid or str(sid) != str(session_id):
                continue
            for fe in row.get("files", []) or []:
                if not isinstance(fe, dict):
                    continue
                for conv in fe.get("conversations", []) or []:
                    if not isinstance(conv, dict):
                        continue
                    cid = conv.get("id")
                    if isinstance(cid, str) and cid and cid not in seen_set:
                        seen.append(cid)
                        seen_set.add(cid)
    except OSError:
        pass
    return seen


# -------------------------------------------------------------------
# Summarize a single conversation_id (used by hooks + manual regenerate)
# -------------------------------------------------------------------

def _summarize_id(
    project_id: str,
    conversation_id: str,
    command: str,
    timeout_seconds: int,
    session_id: str | None,
) -> str | None:
    text = _read_transcript_from_cache(project_id, conversation_id)
    if not text:
        return None
    summary = generate_summary_text(text, command, timeout_seconds=timeout_seconds)
    if summary:
        append_summary(project_id, conversation_id, summary, session_id=session_id)
    return summary


def run_session_summary_hook(data: dict[str, Any]) -> None:
    """Called from ``record`` on stop / agent-response / Cursor ``sessionEnd``; never raises.

    Reads the latest cached transcript bytes (the session-end dispatch in
    ``record`` snapshots the transcript before calling us) and, if
    ``summary.enabled``, pipes them to the configured summary command.
    The result is stored keyed by ``conversation_id``.
    """
    try:
        from .config import get_project_config
        from .record import project_dir_for_summary_hook, transcript_path_from_hook

        cwd = project_dir_for_summary_hook(data)
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

        session_id = str(
            data.get("conversation_id") or data.get("session_id") or "",
        ).strip() or None

        pid = resolve_project_id(cwd, create=False)
        if not pid:
            return

        transcript_path = transcript_path_from_hook(data)
        if not transcript_path:
            return

        cid = compute_conversation_id(transcript_path)
        text = _read_transcript_from_cache(pid, cid)
        if not text:
            return

        summary = generate_summary_text(text, command, timeout_seconds=timeout)
        if summary:
            append_summary(pid, cid, summary, session_id=session_id)
        else:
            _log("summary command failed or produced no output")
    except Exception as exc:
        _log(f"summary hook error: {exc}")


def run_summary_generate(
    project_dir: str,
    *,
    conversation_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, str] | None:
    """Manual regeneration. Pass ``conversation_id`` (one id) or ``session_id``
    (every id referenced by that session's traces). Returns ``{conversation_id: summary}``."""
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

    ids: list[str] = []
    if conversation_id:
        ids = [conversation_id]
    elif session_id:
        ids = _conversation_ids_for_session(pid, session_id)
    if not ids:
        return None

    out: dict[str, str] = {}
    for cid in ids:
        s = _summarize_id(pid, cid, command, timeout, session_id)
        if s:
            out[cid] = s
    return out or None
