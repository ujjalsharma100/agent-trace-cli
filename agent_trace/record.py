"""
Trace recording — reads hook events from stdin, constructs trace records,
and stores them locally (JSONL) or sends them to the remote service.

No external dependencies — uses urllib from the standard library.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import get_auth_token, get_project_config, get_service_url
from .trace import compute_range_positions, create_trace, get_workspace_root


# -------------------------------------------------------------------
# Session edit sequence tracking
# -------------------------------------------------------------------

def _get_next_sequence(session_id: str, project_dir: str | None = None) -> int:
    """Return the next edit sequence number for a session, incrementing atomically.

    Stores state in ``.agent-trace/session-state.json`` as ``{"seq:<session_id>": N}``.
    """
    if not session_id:
        return 0
    if project_dir is None:
        project_dir = get_workspace_root()
    state_path = Path(project_dir) / ".agent-trace" / "session-state.json"
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
    seq = _get_next_sequence(session_id) if session_id else None
    return create_trace(
        "ai", fp,
        model=d.get("model"),
        range_positions=compute_range_positions(edits, fc),
        range_contents=[e["new_string"] for e in edits if e.get("new_string")],
        transcript=d.get("transcript_path"),
        metadata={"conversation_id": d.get("conversation_id"), "generation_id": d.get("generation_id")},
        edit_sequence=seq,
    ), "afterFileEdit"


def _cursor_afterTabFileEdit(d):
    edits = d.get("edits", [])
    session_id = d.get("conversation_id") or ""
    seq = _get_next_sequence(session_id) if session_id else None
    return create_trace(
        "ai", d.get("file_path", ""),
        model=d.get("model"),
        range_positions=compute_range_positions(edits),
        range_contents=[e["new_string"] for e in edits if e.get("new_string")],
        metadata={"conversation_id": d.get("conversation_id"), "generation_id": d.get("generation_id")},
        edit_sequence=seq,
    ), "afterTabFileEdit"


def _cursor_afterShellExecution(d):
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
    ), "afterShellExecution"


def _cursor_sessionStart(d):
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
    ), "sessionStart"


def _cursor_sessionEnd(d):
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
    is_file = tn in ("Write", "Edit")
    is_bash = tn == "Bash"
    if not is_file and not is_bash:
        return None, "PostToolUse"

    ti = d.get("tool_input", {})
    fp = ".shell-history" if is_bash else ti.get("file_path", ".unknown")

    rp, rc = None, None
    if is_file and ti.get("new_string"):
        edits = [{"old_string": ti.get("old_string", ""), "new_string": ti["new_string"]}]
        fc = _try_read_file(ti.get("file_path", "")) if ti.get("file_path") else None
        rp = compute_range_positions(edits, fc)
        rc = [ti["new_string"]]

    session_id = d.get("session_id") or ""
    seq = _get_next_sequence(session_id) if session_id else None

    return create_trace(
        "ai", fp,
        model=d.get("model"),
        range_positions=rp,
        range_contents=rc,
        transcript=d.get("transcript_path"),
        metadata={
            "session_id": d.get("session_id"),
            "tool_name": tn,
            "tool_use_id": d.get("tool_use_id"),
            "command": ti.get("command") if is_bash else None,
        },
        edit_sequence=seq,
    ), "PostToolUse"


def _claude_SessionStart(d):
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_start",
            "session_id": d.get("session_id"),
            "source": d.get("source"),
        },
    ), "SessionStart"


def _claude_SessionEnd(d):
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        metadata={
            "event": "session_end",
            "session_id": d.get("session_id"),
            "reason": d.get("reason"),
        },
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
    """Append trace to .agent-trace/traces.jsonl."""
    if project_dir is None:
        project_dir = get_workspace_root()
    d = Path(project_dir) / ".agent-trace"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "traces.jsonl", "a") as f:
        f.write(json.dumps(trace) + "\n")


def _store_remote(trace, hook_event, config):
    """POST trace to the remote agent-trace-service (stdlib urllib)."""
    project_id = config.get("project_id")
    auth_token = get_auth_token(config)
    service_url = get_service_url(config)

    if not project_id or not auth_token:
        return

    conv_contents = _collect_conversation_contents(trace)
    body: dict = {"project_id": project_id, "trace": trace}
    if conv_contents:
        body["conversation_contents"] = conv_contents

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{service_url}/api/v1/traces",
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {auth_token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()  # drain
    except urllib.error.HTTPError as e:
        print(f"agent-trace: service responded {e.code}: {e.read().decode()}", file=sys.stderr)
    except Exception as e:
        # Never break the coding agent
        print(f"agent-trace: service unreachable: {e}", file=sys.stderr)


def _sync_conversation_remote(conversation_contents, config):
    """POST conversation contents only to the remote service (no trace)."""
    project_id = config.get("project_id")
    auth_token = get_auth_token(config)
    service_url = get_service_url(config)

    if not project_id or not auth_token or not conversation_contents:
        return

    body = {
        "project_id": project_id,
        "conversation_contents": conversation_contents,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{service_url}/api/v1/conversations/sync",
        data=data,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {auth_token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as e:
        print(f"agent-trace: conversation sync failed {e.code}: {e.read().decode()}", file=sys.stderr)
    except Exception as e:
        print(f"agent-trace: conversation sync unreachable: {e}", file=sys.stderr)


def _sync_conversation_only(data):
    """
    Sync conversation content to remote after the agent has finished a response.
    Only runs when storage is remote and we have a local transcript path.
    Does not create or store a trace.
    """
    config = get_project_config()
    if config is None:
        return
    if config.get("storage", "local") != "remote":
        return

    transcript_path = data.get("transcript_path")
    if not transcript_path or not isinstance(transcript_path, str):
        return

    # Resolve to absolute path so URL is stable
    workspace_roots = data.get("workspace_roots") or []
    if workspace_roots and not os.path.isabs(transcript_path):
        root = workspace_roots[0]
        abs_path = os.path.abspath(os.path.join(root, transcript_path))
    else:
        abs_path = os.path.abspath(transcript_path)

    content = _try_read_file(abs_path)
    if content is None:
        return

    url = "file://" + abs_path
    conversation_contents = [{"url": url, "content": content}]
    _sync_conversation_remote(conversation_contents, config)


# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------

def record_from_stdin():
    """Read a hook event from stdin, build a trace, and store it (or sync conversation only)."""
    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = data.get("hook_event_name", "")

    # Conversation-sync-only events (no trace record):
    #   - afterAgentResponse  : Cursor native — fires after each assistant message
    #   - Stop                : Claude Code native — fires when the agent loop ends
    #   - stop                : Cursor mapping of Claude Code's Stop
    if event in ("afterAgentResponse", "Stop", "stop"):
        _sync_conversation_only(data)
        return

    handler = _CURSOR.get(event) or _CLAUDE.get(event)
    if handler is None:
        return

    trace, hook_event = handler(data)
    if trace is None:
        return

    config = get_project_config()
    if config is None:
        return  # not initialised — silent exit

    storage = config.get("storage", "local")
    if storage == "local":
        _store_local(trace)
    elif storage == "remote":
        _store_remote(trace, hook_event, config)
