"""
Trace recording — reads hook events from stdin, constructs trace records,
and stores them locally (JSONL).

Hooks write *only* to local JSONL.  Network calls happen exclusively via
``sync.py`` (``agent-trace push`` / ``agent-trace pull``).

No external dependencies — uses only the Python standard library.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import get_project_config
from .session import touch_session_project
from .storage import (
    ensure_project_dir,
    get_session_state_path,
    get_traces_path,
    resolve_project_id,
)
from .trace import (
    compute_range_positions,
    create_trace,
    get_workspace_root,
    resolve_file_project,
)


# -------------------------------------------------------------------
# Session edit sequence tracking
# -------------------------------------------------------------------

def _get_next_sequence(session_id: str, project_dir: str | None = None) -> int:
    """Return the next edit sequence number for a session, incrementing atomically.

    State lives in ``<AGENT_TRACE_HOME>/projects/<id>/session-state.json``
    as ``{"seq:<session_id>": N}``.
    """
    if not session_id:
        return 0
    if project_dir is None:
        project_dir = get_workspace_root()
    pid = resolve_project_id(project_dir, create=True)
    if not pid:
        return 0
    ensure_project_dir(pid)
    state_path = get_session_state_path(pid)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            state = {}

    key = f"seq:{session_id}"
    seq = state.get(key, 0)
    state[key] = seq + 1

    try:
        state_path.write_text(json.dumps(state))
    except OSError:
        pass

    return seq


# -------------------------------------------------------------------
# File helpers
# -------------------------------------------------------------------

def _try_read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


def _file_existed_before(file_path: str) -> bool:
    """Whether the file existed before this edit (for metadata)."""
    try:
        return Path(file_path).exists()
    except OSError:
        return False


def _ranges_from_write(_file_path: str, content: str) -> tuple[list | None, list | None]:
    """For Write tool: entire file is the new content."""
    if not content:
        return None, None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    line_count = normalized.count("\n") + (0 if normalized.endswith("\n") else 1)
    if line_count <= 0:
        return None, None
    rp = [{"start_line": 1, "end_line": line_count}]
    rc = [content]
    return rp, rc


def _ranges_from_edit(file_path: str, old_string: str, new_string: str) -> tuple[list | None, list | None]:
    """For Edit tool: single old/new pair."""
    if not new_string:
        return None, None
    edits = [{"old_string": old_string, "new_string": new_string}]
    fc = _try_read_file(file_path) if file_path else None
    rp = compute_range_positions(edits, fc)
    rc = [new_string]
    return rp, rc


def _ranges_from_multiedit(file_path: str, edits: list) -> tuple[list | None, list | None]:
    """For MultiEdit: replay edits in order, emit one range per edit."""
    if not edits:
        return None, None
    buffer = _try_read_file(file_path) or ""
    rp: list[dict] = []
    rc: list[str] = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        if not new:
            continue
        if old == "":
            start = 1
            end = new.count("\n") + 1
        else:
            idx = buffer.find(old)
            if idx < 0:
                continue
            start = buffer[:idx].count("\n") + 1
            end = start + new.count("\n")
        rp.append({"start_line": start, "end_line": end})
        rc.append(new)
        buffer = buffer.replace(old, new, 1) if old else new
    return (rp if rp else None), (rc if rc else None)


def _ranges_from_notebook(_notebook_path: str, ti: dict) -> tuple[list | None, list | None]:
    """For NotebookEdit: cell source as content; line numbers are cell-local."""
    new_source = ti.get("new_source", "")
    if not new_source:
        return None, None
    line_count = new_source.count("\n") + 1
    rp = [{"start_line": 1, "end_line": line_count}]
    rc = [new_source]
    return rp, rc


def _collect_conversation_contents(trace):
    """Walk all files→conversations, read local file:// URLs (deduplicated)."""
    seen: dict[str, str | None] = {}  # url → content (or None if unreadable)
    for fe in trace.get("files", []):
        for conv in fe.get("conversations", []):
            url = conv.get("url", "")
            if not url or url in seen:
                continue
            if url.startswith("file://"):
                local = url[7:]
                content = _try_read_file(local)
                seen[url] = content
    # Build the array — only include entries where we got content
    return [{"url": u, "content": c} for u, c in seen.items() if c is not None] or None


# -------------------------------------------------------------------
# Cursor event handlers
# -------------------------------------------------------------------

def _cursor_afterFileEdit(d):
    edits = d.get("edits", [])
    fp = d.get("file_path", "")
    fc = _try_read_file(fp) if fp else None
    session_id = d.get("conversation_id") or ""
    res = resolve_file_project(fp)
    seq = _get_next_sequence(session_id, res.repo_root if res else None) if session_id else None
    return create_trace(
        "ai", fp,
        model=d.get("model"),
        range_positions=compute_range_positions(edits, fc),
        range_contents=[e["new_string"] for e in edits if e.get("new_string")],
        transcript=d.get("transcript_path"),
        metadata={"conversation_id": d.get("conversation_id"), "generation_id": d.get("generation_id")},
        edit_sequence=seq,
        resolution=res,
    ), "afterFileEdit"


def _cursor_afterTabFileEdit(d):
    edits = d.get("edits", [])
    fp = d.get("file_path", "")
    session_id = d.get("conversation_id") or ""
    res = resolve_file_project(fp)
    seq = _get_next_sequence(session_id, res.repo_root if res else None) if session_id else None
    return create_trace(
        "ai", fp,
        model=d.get("model"),
        range_positions=compute_range_positions(edits),
        range_contents=[e["new_string"] for e in edits if e.get("new_string")],
        metadata={"conversation_id": d.get("conversation_id"), "generation_id": d.get("generation_id")},
        edit_sequence=seq,
        resolution=res,
    ), "afterTabFileEdit"


def _cursor_afterShellExecution(d):
    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".shell-history", anchor_path=anchor)
    return create_trace(
        "ai", ".shell-history",
        model=d.get("model"),
        transcript=d.get("transcript_path"),
        metadata={
            "conversation_id": d.get("conversation_id"),
            "generation_id": d.get("generation_id"),
            "command": d.get("command"),
            "duration_ms": d.get("duration"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "afterShellExecution"


def _cursor_sessionStart(d):
    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_start",
            "session_id": d.get("session_id"),
            "conversation_id": d.get("conversation_id"),
            "is_background_agent": d.get("is_background_agent"),
            "composer_mode": d.get("composer_mode"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "sessionStart"


def _cursor_sessionEnd(d):
    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_end",
            "session_id": d.get("session_id"),
            "conversation_id": d.get("conversation_id"),
            "reason": d.get("reason"),
            "duration_ms": d.get("duration_ms"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "sessionEnd"


_CURSOR = {
    "afterFileEdit": _cursor_afterFileEdit,
    "afterTabFileEdit": _cursor_afterTabFileEdit,
    "afterShellExecution": _cursor_afterShellExecution,
    "sessionStart": _cursor_sessionStart,
    "sessionEnd": _cursor_sessionEnd,
}


# -------------------------------------------------------------------
# Claude Code event handlers
# -------------------------------------------------------------------

def _claude_PostToolUse(d):
    tn = d.get("tool_name", "")
    if tn == "Bash":
        ti = d.get("tool_input", {})
        session_id = d.get("session_id") or ""
        anchor = ti.get("cwd") or d.get("cwd") or os.getcwd()
        res = resolve_file_project(".shell-history", anchor_path=anchor)
        seq = _get_next_sequence(session_id, res.repo_root if res else None) if session_id else None
        return create_trace(
            "ai", ".shell-history",
            model=d.get("model"),
            transcript=d.get("transcript_path"),
            metadata={
                "session_id": d.get("session_id"),
                "tool_name": tn,
                "tool_use_id": d.get("tool_use_id"),
                "command": ti.get("command"),
            },
            edit_sequence=seq,
            anchor_path=anchor,
            resolution=res,
        ), "PostToolUse"

    ti = d.get("tool_input", {})
    fp = ti.get("file_path") or ti.get("notebook_path") or ".unknown"
    anchor = d.get("cwd") or os.getcwd()

    if tn == "Write":
        existed = _file_existed_before(fp)
        rp, rc = _ranges_from_write(fp, ti.get("content", ""))
        meta_extra: dict = {
            "is_creation": not existed,
        }
    elif tn == "Edit":
        rp, rc = _ranges_from_edit(fp, ti.get("old_string", ""), ti.get("new_string", ""))
        meta_extra = {}
    elif tn == "MultiEdit":
        rp, rc = _ranges_from_multiedit(fp, ti.get("edits", []))
        meta_extra = {}
    elif tn == "NotebookEdit":
        fp = ti.get("notebook_path", fp)
        rp, rc = _ranges_from_notebook(fp, ti)
        cell_id = ti.get("cell_id")
        meta_extra = {"cell_id": cell_id} if cell_id is not None else {}
    else:
        return None, "PostToolUse"

    if rp is None or rc is None:
        return None, "PostToolUse"

    session_id = d.get("session_id") or ""
    res = resolve_file_project(fp, anchor_path=anchor)
    seq = _get_next_sequence(session_id, res.repo_root if res else None) if session_id else None

    metadata: dict = {
        "session_id": d.get("session_id"),
        "tool_name": tn,
        "tool_use_id": d.get("tool_use_id"),
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}
    metadata.update(meta_extra)

    return create_trace(
        "ai", fp,
        model=d.get("model"),
        range_positions=rp,
        range_contents=rc,
        transcript=d.get("transcript_path"),
        metadata=metadata,
        edit_sequence=seq,
        anchor_path=anchor,
        resolution=res,
    ), "PostToolUse"


def _claude_SessionStart(d):
    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_start",
            "session_id": d.get("session_id"),
            "source": d.get("source"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "SessionStart"


def _claude_SessionEnd(d):
    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_end",
            "session_id": d.get("session_id"),
            "reason": d.get("reason"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "SessionEnd"


_CLAUDE = {
    "PostToolUse": _claude_PostToolUse,
    "SessionStart": _claude_SessionStart,
    "SessionEnd": _claude_SessionEnd,
}


# -------------------------------------------------------------------
# Storage backends
# -------------------------------------------------------------------

def _store_local(trace, project_dir=None):
    """Append trace to <AGENT_TRACE_HOME>/projects/<id>/traces.jsonl."""
    meta = trace.get("metadata") or {}
    pid = meta.get("project_id")
    if not pid:
        if project_dir is None:
            project_dir = meta.get("repo_root") or get_workspace_root()
        pid = resolve_project_id(project_dir, create=True)
    if not pid:
        return
    ensure_project_dir(pid)
    path = get_traces_path(pid)
    with open(path, "a") as f:
        f.write(json.dumps(trace) + "\n")




# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------

def record_from_stdin():
    """Read a hook event from stdin, build a trace, and store it locally.

    Hooks write *only* to local JSONL.  Network sync happens via
    ``agent-trace push`` / ``agent-trace pull``.
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = data.get("hook_event_name", "")

    # Session-end: optional pluggable summary (Phase 6); no trace record.
    # Conversation content syncs via ``agent-trace push``.
    if event in ("afterAgentResponse", "Stop", "stop"):
        try:
            from .summary import run_session_summary_hook

            run_session_summary_hook(data)
        except Exception:
            pass
        return

    handler = _CURSOR.get(event) or _CLAUDE.get(event)
    if handler is None:
        return

    trace, hook_event = handler(data)
    if trace is None:
        return

    meta = trace.get("metadata") or {}
    repo_root = meta.get("repo_root")
    config = get_project_config(project_dir=repo_root) if repo_root else get_project_config()
    if config is None:
        return

    sid = (
        meta.get("session_id")
        or meta.get("conversation_id")
        or data.get("conversation_id")
        or data.get("session_id")
    )
    pid = meta.get("project_id")
    if sid and pid:
        tool = trace.get("tool") if isinstance(trace.get("tool"), dict) else {}
        touch_session_project(
            str(sid),
            str(pid),
            tool_name=(tool or {}).get("name"),
            transcript_path=data.get("transcript_path"),
        )

    _store_local(trace, project_dir=repo_root)
