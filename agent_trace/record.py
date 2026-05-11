"""
Trace recording — reads hook events from stdin, dispatches them to the
right adapter's translator, and stores the resulting trace locally.

This module is **agent-agnostic**: it knows nothing about Cursor,
Claude, Codex, or any future harness. The dispatcher walks the adapter
registry — each adapter declares which hook event names it owns
(``adapter.EVENTS``), which env vars hold the transcript path
(``transcript_env_vars``), which env vars hold the workspace dir
(``project_dir_env_vars``), and which events should fire the summary
command (``summary_only_events`` / ``summary_then_trace_events``).

What stays here are *generic* helpers used by every adapter's
translators: edit-sequence tracking, file IO, range computation, the
final ``_store_local`` writer.

Hooks write *only* to local JSONL. Network calls happen exclusively via
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
# Hook payload helpers (registry-driven)
# -------------------------------------------------------------------

def transcript_path_from_hook(data: dict) -> str | None:
    """Resolve the conversation transcript path from a hook payload.

    Order of precedence:
      1. ``data["transcript_path"]`` (Cursor sets this on most hooks)
      2. The first hit across every adapter's ``transcript_env_vars``
         (e.g. Cursor sets ``CURSOR_TRANSCRIPT_PATH`` for ``sessionEnd``
         / ``Stop`` where the JSON omits the path).

    New harnesses contribute their own env-var name on the adapter —
    this function never names a tool.
    """
    tp = data.get("transcript_path")
    if isinstance(tp, str) and tp.strip():
        return tp.strip()
    from .hooks import iter_adapters

    for adapter in iter_adapters():
        for var in getattr(adapter, "transcript_env_vars", ()) or ():
            v = os.environ.get(var)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def project_dir_from_hook(data: dict) -> str:
    """Resolve the workspace dir from a hook payload.

    Order: ``data["cwd"]`` → adapter-declared env vars (e.g.
    ``CURSOR_PROJECT_DIR``, ``CLAUDE_PROJECT_DIR``) →
    ``data["workspace_roots"][0]`` → ``os.getcwd()``.
    """
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    from .hooks import iter_adapters

    for adapter in iter_adapters():
        for var in getattr(adapter, "project_dir_env_vars", ()) or ():
            v = os.environ.get(var)
            if isinstance(v, str) and v.strip():
                return v.strip()
    roots = data.get("workspace_roots")
    if isinstance(roots, list) and roots:
        r0 = roots[0]
        if isinstance(r0, str) and r0.strip():
            return r0.strip()
    return os.getcwd()


def project_dir_for_summary_hook(data: dict) -> str:
    """Git root for summary config and storage: prefer session's primary touched repo.

    File traces resolve the repo from edited paths (nested repos → inner root). Hooks
    often pass only workspace ``cwd``, which may be a parent folder whose git root is
    the outer monorepo — wrong ``project_id``. Session manifests record which
    ``project_id`` actually received edits; match that when possible.
    """
    from .registry import get_project_record
    from .session import primary_project_id_for_session

    sid = str(
        data.get("conversation_id") or data.get("session_id") or "",
    ).strip()
    if sid:
        pid = primary_project_id_for_session(sid)
        if pid and not pid.startswith("detached:"):
            rec = get_project_record(pid)
            if rec:
                root = rec.get("canonical_root")
                if isinstance(root, str) and root.strip() and os.path.isdir(root.strip()):
                    return os.path.realpath(root.strip())
    return project_dir_from_hook(data)


# -------------------------------------------------------------------
# Session edit sequence tracking (shared across all adapters)
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
# File / range helpers (shared)
# -------------------------------------------------------------------

def _try_read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


def _file_existed_before(file_path: str) -> bool:
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


# -------------------------------------------------------------------
# Storage backend
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
# Back-compat re-exports
# -------------------------------------------------------------------
#
# Older code (and tests) imports translator functions directly from
# ``agent_trace.record``. The translators now live next to their
# adapter, but we keep stable aliases here so nothing breaks.

from .hooks.claude import (  # noqa: E402
    _PostToolUse as _claude_PostToolUse,
    _SessionEnd as _claude_SessionEnd,
    _SessionStart as _claude_SessionStart,
    _model_from_transcript_tail as _model_from_claude_transcript_tail,
)
from .hooks.codex import (  # noqa: E402
    _SessionStart as _codex_SessionStart,
    _TurnComplete as _codex_TurnComplete,
)
from .hooks.cursor import (  # noqa: E402
    _afterFileEdit as _cursor_afterFileEdit,
    _afterShellExecution as _cursor_afterShellExecution,
    _afterTabFileEdit as _cursor_afterTabFileEdit,
    _sessionEnd as _cursor_sessionEnd,
    _sessionStart as _cursor_sessionStart,
)


# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------

def record_from_stdin():
    """Read a hook event from stdin, build a trace, and store it locally.

    Hooks write *only* to local JSONL. Network sync happens via
    ``agent-trace push`` / ``agent-trace pull``.

    Dispatch is fully registry-driven:
      1. The event-name to translator mapping comes from each adapter's
         ``EVENTS`` dict.
      2. Whether the event triggers the summary command — and whether
         it should *also* be dispatched as a regular trace — comes from
         each adapter's ``summary_only_events`` /
         ``summary_then_trace_events`` declarations.
      3. Each adapter optionally runs ``pre_summary_hook`` before the
         summary command (e.g. Claude refreshes its model cache).
    """
    raw = sys.stdin.read().strip()
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = data.get("hook_event_name", "") or ""

    from .hooks import iter_adapters

    # Phase 1: session-end / summary classification.
    summary_owner = None
    also_dispatch = False
    for adapter in iter_adapters():
        triggers, dispatch = adapter.is_session_end(event)
        if triggers:
            summary_owner = adapter
            also_dispatch = dispatch
            break

    if summary_owner is not None:
        try:
            summary_owner.pre_summary_hook(data)
        except Exception:
            pass
        try:
            from .summary import run_session_summary_hook

            run_session_summary_hook(data)
        except Exception:
            pass
        if not also_dispatch:
            return
        # Fall through so the event also reaches its adapter's
        # translator (e.g. Cursor's ``sessionEnd`` records a trace).

    # Phase 2: trace handler dispatch via the registry.
    handler = None
    for adapter in iter_adapters():
        events = getattr(adapter, "EVENTS", None) or {}
        if event in events:
            handler = events[event]
            break
    if handler is None:
        return

    trace, _hook_event = handler(data)
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
            transcript_path=transcript_path_from_hook(data),
        )

    _store_local(trace, project_dir=repo_root)
