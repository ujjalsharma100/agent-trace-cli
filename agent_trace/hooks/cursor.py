"""
Cursor adapter — hooks injection, event translators, summary lifecycle.

Everything Cursor-specific lives here:
  - hook config writer / remover
  - hook events Cursor emits (``afterFileEdit`` etc.) and their translators
  - env vars Cursor sets (``CURSOR_TRANSCRIPT_PATH``, ``CURSOR_PROJECT_DIR``)
  - which events trigger the summary command (``afterAgentResponse``,
    ``sessionEnd``)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .base import AGENT_TRACE_CMD, CodingAgentAdapter


CURSOR_HOOKS_FILE = ".cursor/hooks.json"
CURSOR_GLOBAL_HOOKS_FILE = Path.home() / ".cursor" / "hooks.json"


# Hook events Cursor emits that we listen to (used by the inject step).
_CURSOR_HOOK_EVENTS = (
    "sessionStart",
    "sessionEnd",
    "afterFileEdit",
    "afterTabFileEdit",
    "afterShellExecution",
    "afterAgentResponse",
)


# ---------------------------------------------------------------------
# Event translators (hook payload → trace record)
# ---------------------------------------------------------------------

def _afterFileEdit(d):
    from ..record import (
        _get_next_sequence,
        _try_read_file,
        transcript_path_from_hook,
    )
    from ..trace import (
        compute_range_positions,
        create_trace,
        resolve_file_project,
    )

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
        transcript=transcript_path_from_hook(d),
        metadata={"conversation_id": d.get("conversation_id"), "generation_id": d.get("generation_id")},
        edit_sequence=seq,
        resolution=res,
    ), "afterFileEdit"


def _afterTabFileEdit(d):
    from ..record import _get_next_sequence
    from ..trace import (
        compute_range_positions,
        create_trace,
        resolve_file_project,
    )

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


def _afterShellExecution(d):
    from ..record import project_dir_from_hook, transcript_path_from_hook
    from ..trace import create_trace, resolve_file_project

    anchor = project_dir_from_hook(d)
    res = resolve_file_project(".shell-history", anchor_path=anchor)
    return create_trace(
        "ai", ".shell-history",
        model=d.get("model"),
        transcript=transcript_path_from_hook(d),
        metadata={
            "conversation_id": d.get("conversation_id"),
            "generation_id": d.get("generation_id"),
            "command": d.get("command"),
            "duration_ms": d.get("duration"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "afterShellExecution"


def _sessionStart(d):
    from ..record import project_dir_from_hook
    from ..trace import create_trace, resolve_file_project

    anchor = project_dir_from_hook(d)
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


def _sessionEnd(d):
    from ..record import project_dir_from_hook, transcript_path_from_hook
    from ..trace import create_trace, resolve_file_project

    anchor = project_dir_from_hook(d)
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model"),
        transcript=transcript_path_from_hook(d),
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


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------

class CursorAdapter(CodingAgentAdapter):
    name = "cursor"
    display_name = "Cursor"
    rules_dir = ".cursor/rules"
    rule_extension = ".mdc"

    EVENTS = {
        "afterFileEdit": _afterFileEdit,
        "afterTabFileEdit": _afterTabFileEdit,
        "afterShellExecution": _afterShellExecution,
        "sessionStart": _sessionStart,
        "sessionEnd": _sessionEnd,
    }

    # ``afterAgentResponse`` is "agent finished a turn" in Cursor; no
    # trace record, but it should fire the summary command.
    summary_only_events = ("afterAgentResponse",)
    # ``sessionEnd`` *both* ends the session (-> summary) and gets
    # routed to the trace handler so we record a session-end trace.
    summary_then_trace_events = ("sessionEnd",)

    transcript_env_vars = ("CURSOR_TRANSCRIPT_PATH",)
    project_dir_env_vars = ("CURSOR_PROJECT_DIR",)

    def global_config_path(self) -> Path:
        return CURSOR_GLOBAL_HOOKS_FILE

    def project_config_paths(self) -> tuple[str, ...]:
        return (CURSOR_HOOKS_FILE,)

    def detect_tool_info(self) -> dict | None:
        ver = os.environ.get("CURSOR_VERSION")
        if ver:
            return {"name": "cursor", "version": ver}
        return None

    def inject(
        self,
        record_invocation: str,
        project_dir: str | None = None,
        *,
        global_install: bool = False,
    ) -> bool:
        if global_install:
            hooks_path = CURSOR_GLOBAL_HOOKS_FILE
        else:
            if project_dir is None:
                project_dir = os.getcwd()
            hooks_path = Path(project_dir) / CURSOR_HOOKS_FILE

        hooks_path.parent.mkdir(parents=True, exist_ok=True)

        if hooks_path.exists():
            try:
                config = json.loads(hooks_path.read_text())
            except (json.JSONDecodeError, OSError):
                config = {}
        else:
            config = {}

        config.setdefault("version", 1)
        config.setdefault("hooks", {})

        for event in _CURSOR_HOOK_EVENTS:
            existing = config["hooks"].get(event, [])
            already = any(
                record_invocation in (h.get("command", "") if isinstance(h, dict) else "")
                for h in existing
            )
            if not already:
                existing.append({"command": record_invocation})
                config["hooks"][event] = existing

        hooks_path.write_text(json.dumps(config, indent=2) + "\n")
        return True

    def remove(self) -> bool:
        return _remove_agent_trace_from_cursor(CURSOR_GLOBAL_HOOKS_FILE)

    def is_installed(self, *, global_only: bool = True) -> bool:
        try:
            raw = CURSOR_GLOBAL_HOOKS_FILE.read_text()
        except (OSError, FileNotFoundError):
            return False
        return AGENT_TRACE_CMD in raw


def _remove_agent_trace_from_cursor(hooks_path: Path) -> bool:
    if not hooks_path.is_file():
        return False
    try:
        config = json.loads(hooks_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        filtered = [
            h for h in entries
            if not (isinstance(h, dict) and AGENT_TRACE_CMD in h.get("command", ""))
        ]
        if len(filtered) != len(entries):
            changed = True
            if filtered:
                hooks[event] = filtered
            else:
                del hooks[event]

    if changed:
        hooks_path.write_text(json.dumps(config, indent=2) + "\n")
    return changed
