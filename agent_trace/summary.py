"""
URL-keyed transcript summaries — pluggable summarization of conversation
transcripts referenced by traces.

For each session, the conversation transcript file (whatever the agent
writes to ``transcript_path`` — Claude Code JSONL, Cursor's equivalent,
etc.) is read as raw text and piped to a user-configured command on
stdin. The command's stdout is treated as the summary text and stored
keyed by ``conversation_url`` (``file://<transcript_path>``). The
schema of the transcript is opaque to agent-trace; the command decides
what to do with it.

Opt-in via ``project-config.json`` → ``summary.enabled`` and
``summary.command``. Storage: ``session-summaries.jsonl`` under the
project dir, one row per (url, summary). Failures never raise through
hooks.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from .storage import (
    ensure_project_dir,
    get_session_summaries_path,
    get_traces_path,
    resolve_project_id,
)


def _log(msg: str) -> None:
    print(f"agent-trace: {msg}", file=sys.stderr)


def _read_transcript(conversation_url: str) -> str | None:
    """Read transcript bytes from a ``file://`` URL. Returns ``None`` if unreadable."""
    if not conversation_url or not conversation_url.startswith("file://"):
        return None
    path = conversation_url[len("file://"):]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


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
        )
    except subprocess.TimeoutExpired:
        return None
    except (OSError, ValueError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    return out or None


def append_summary(
    project_id: str,
    conversation_url: str,
    summary: str,
    *,
    session_id: str | None = None,
) -> None:
    """Append one row to ``session-summaries.jsonl``: ``{conversation_url, summary, ...}``."""
    if not conversation_url or not summary:
        return
    ensure_project_dir(project_id)
    path = get_session_summaries_path(project_id)
    row: dict[str, Any] = {
        "conversation_url": conversation_url,
        "summary": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if session_id:
        row["session_id"] = session_id
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def latest_summary_by_url(project_id: str) -> dict[str, str]:
    """Map ``conversation_url`` → latest summary across all rows."""
    path = get_session_summaries_path(project_id)
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = row.get("conversation_url")
            summary = row.get("summary")
            if isinstance(url, str) and isinstance(summary, str) and url and summary:
                out[url] = summary
    except OSError:
        pass
    return out


def get_summary_for_url(project_dir: str, conversation_url: str) -> str | None:
    """Lookup a single URL's latest summary."""
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None
    return latest_summary_by_url(pid).get(conversation_url)


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


def _urls_in_ledger(ledger: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    files = ledger.get("files", {})
    if not isinstance(files, dict):
        return urls
    for fl in files.values():
        if not isinstance(fl, dict):
            continue
        for seg in fl.get("line_attributions", []):
            if not isinstance(seg, dict):
                continue
            url = seg.get("conversation_url")
            if isinstance(url, str) and url:
                urls.add(url)
    return urls


def get_summary_for_commit(project_id: str, commit_sha: str) -> dict[str, str] | None:
    """``{conversation_url: summary}`` for every URL referenced by the commit's ledger."""
    led = _ledger_for_commit(project_id, commit_sha)
    if not led:
        return None
    urls = _urls_in_ledger(led)
    if not urls:
        return None
    by_url = latest_summary_by_url(project_id)
    out = {u: by_url[u] for u in urls if u in by_url}
    return out if out else None


def merge_note_summaries(
    project_dir: str,
    ledger: dict[str, Any],
    static_summaries: dict[str, Any] | None = None,  # accepted for caller compat; ignored
) -> dict[str, str] | None:
    """Resolve URL→summary for the git note. Static map is ignored."""
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
    ``conversation_url`` from traces in the same staging window as the ledger,
    with the latest stored summary per URL (if any).

    Unlike :func:`get_summary_for_commit`, this is not limited to URLs that
    appear in attributed line segments.
    """
    from .ledger import list_traces_in_staging_window

    parent_sha = ledger.get("parent_sha")
    parent_at = ledger.get("parent_committed_at")
    committed_at = ledger.get("committed_at")
    raw = list_traces_in_staging_window(
        project_dir,
        str(parent_sha) if parent_sha else None,
        str(parent_at) if parent_at else None,
        str(committed_at) if committed_at else None,
    )
    urls: list[str] = []
    seen: set[str] = set()
    for t in raw:
        for fe in t.get("files") or []:
            if not isinstance(fe, dict):
                continue
            for conv in fe.get("conversations") or []:
                if not isinstance(conv, dict):
                    continue
                url = conv.get("url")
                if isinstance(url, str) and url and url not in seen:
                    seen.add(url)
                    urls.append(url)
    if not urls:
        return None
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return [{"conversation_url": u, "summary": None} for u in urls]
    by_url = latest_summary_by_url(pid)
    out: list[dict[str, Any]] = []
    for u in urls:
        s = by_url.get(u)
        row: dict[str, Any] = {"conversation_url": u}
        if s is not None:
            row["summary"] = s
        else:
            row["summary"] = None
        out.append(row)
    return out


def _conversation_urls_for_session(project_id: str, session_id: str) -> list[str]:
    """All distinct ``conversation_url``s referenced by traces in this session, in first-seen order."""
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
                    url = conv.get("url")
                    if isinstance(url, str) and url and url not in seen_set:
                        seen.append(url)
                        seen_set.add(url)
    except OSError:
        pass
    return seen


def _summarize_url(
    project_id: str,
    conversation_url: str,
    command: str,
    timeout_seconds: int,
    session_id: str | None,
) -> str | None:
    text = _read_transcript(conversation_url)
    if not text:
        return None
    summary = generate_summary_text(text, command, timeout_seconds=timeout_seconds)
    if summary:
        append_summary(project_id, conversation_url, summary, session_id=session_id)
    return summary


def run_session_summary_hook(data: dict[str, Any]) -> None:
    """Called from ``record`` on session-end / stop hooks; never raises.

    Reads ``data["transcript_path"]`` from the hook payload, treats the
    file at that path as the transcript, and pipes it to the configured
    summary command. The result is stored keyed by ``file://<path>``.
    """
    try:
        from .config import get_project_config

        cwd = data.get("cwd") or os.getcwd()
        session_id = str(
            data.get("conversation_id") or data.get("session_id") or "",
        ).strip() or None

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

        transcript_path = data.get("transcript_path")
        if not transcript_path:
            return
        url = f"file://{transcript_path}"
        summary = _summarize_url(pid, url, command, timeout, session_id)
        if summary is None:
            _log("summary command failed or produced no output")
    except Exception as exc:
        _log(f"summary hook error: {exc}")


def run_summary_generate(
    project_dir: str,
    *,
    conversation_url: str | None = None,
    session_id: str | None = None,
) -> dict[str, str] | None:
    """Manual regeneration. Pass ``conversation_url`` (one URL) or ``session_id``
    (every URL referenced by that session's traces). Returns ``{url: summary}``."""
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

    urls: list[str] = []
    if conversation_url:
        urls = [conversation_url]
    elif session_id:
        urls = _conversation_urls_for_session(pid, session_id)
    if not urls:
        return None

    out: dict[str, str] = {}
    for url in urls:
        s = _summarize_url(pid, url, command, timeout, session_id)
        if s:
            out[url] = s
    return out or None
