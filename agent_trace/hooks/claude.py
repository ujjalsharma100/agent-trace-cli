"""
Claude Code adapter — hooks injection, event translators, summary
lifecycle, and the Claude-specific session-model tracking helpers.

Claude's ``PostToolUse`` hook payload doesn't include a model id, so we
keep a per-session model cache:
  - ``SessionStart`` writes the model into the cache
  - ``Stop`` / ``stop`` runs the pre-summary hook to refresh the cache
    from the transcript tail (handles mid-session ``/model`` changes)
  - ``PostToolUse`` reads the cached model

All of this lives here, not in ``record.py``, so adding a new agent
never requires touching Claude code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .base import AGENT_TRACE_CMD, CodingAgentAdapter


CLAUDE_SETTINGS_FILE = ".claude/settings.json"
CLAUDE_GLOBAL_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# Claude session JSONL can be large; only scan the tail for the latest
# ``message.model`` field.
_TRANSCRIPT_TAIL_BYTES = 512 * 1024


# ---------------------------------------------------------------------
# Claude session-model cache (persisted under
# ``<AGENT_TRACE_HOME>/projects/<id>/session-state.json`` as
# ``model:<session_id>``).
# ---------------------------------------------------------------------

def _remember_session_model(session_id: str, model: str | None, project_dir: str | None = None) -> None:
    from ..storage import (
        ensure_project_dir,
        get_session_state_path,
        resolve_project_id,
    )
    from ..trace import get_workspace_root

    if not session_id or not model:
        return
    if project_dir is None:
        project_dir = get_workspace_root()
    pid = resolve_project_id(project_dir, create=True)
    if not pid:
        return
    ensure_project_dir(pid)
    state_path = get_session_state_path(pid)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            state = {}
    state[f"model:{session_id}"] = model
    try:
        state_path.write_text(json.dumps(state))
    except OSError:
        pass


def _read_transcript_tail_text(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        with open(path, "rb") as f:
            f.seek(max(0, size - max_bytes))
            f.readline()  # discard incomplete first line
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _extract_model_from_transcript_obj(obj: object, depth: int = 0) -> str | None:
    if depth > 8 or not isinstance(obj, dict):
        return None
    msg = obj.get("message")
    if isinstance(msg, dict):
        for key in ("model", "modelId", "model_id"):
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        inner = _extract_model_from_transcript_obj(msg, depth + 1)
        if inner:
            return inner
    for key in ("model", "modelId", "model_id"):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for nest in ("session", "metadata", "config"):
        inner = obj.get(nest)
        if isinstance(inner, dict):
            got = _extract_model_from_transcript_obj(inner, depth + 1)
            if got:
                return got
    return None


def _model_from_transcript_tail(
    transcript_path: str | None,
    *,
    max_bytes: int = _TRANSCRIPT_TAIL_BYTES,
) -> str | None:
    """Latest model id seen in a Claude transcript JSONL file."""
    if not transcript_path:
        return None
    try:
        p = Path(transcript_path)
        if not p.is_file():
            return None
        text = _read_transcript_tail_text(p, max_bytes)
        if not text:
            return None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            m = _extract_model_from_transcript_obj(obj)
            if m:
                return m
        return None
    except OSError:
        return None


def _session_model_from_state(session_id: str, project_dir: str | None = None) -> str | None:
    from ..storage import get_session_state_path, resolve_project_id
    from ..trace import get_workspace_root

    if not session_id:
        return None
    if project_dir is None:
        project_dir = get_workspace_root()
    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return None
    state_path = get_session_state_path(pid)
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    v = state.get(f"model:{session_id}")
    return str(v) if v else None


def _claude_model(d: dict, project_dir: str | None) -> str | None:
    """Resolve model: hook fields, then transcript, then cached SessionStart."""
    from ..record import transcript_path_from_hook

    m = d.get("model") or d.get("model_id")
    if m:
        return str(m)
    sid = d.get("session_id") or ""
    m = _model_from_transcript_tail(transcript_path_from_hook(d))
    if m:
        if sid and project_dir:
            prev = _session_model_from_state(sid, project_dir)
            if prev != m:
                _remember_session_model(sid, m, project_dir)
        return m
    return _session_model_from_state(sid, project_dir)


# ---------------------------------------------------------------------
# Event translators (hook payload → trace record)
# ---------------------------------------------------------------------

def _PostToolUse(d):
    from ..record import (
        _file_existed_before,
        _get_next_sequence,
        _ranges_from_edit,
        _ranges_from_multiedit,
        _ranges_from_notebook,
        _ranges_from_write,
        transcript_path_from_hook,
    )
    from ..trace import create_trace, resolve_file_project

    tn = d.get("tool_name", "")
    if tn == "Bash":
        ti = d.get("tool_input", {})
        session_id = d.get("session_id") or ""
        anchor = ti.get("cwd") or d.get("cwd") or os.getcwd()
        res = resolve_file_project(".shell-history", anchor_path=anchor)
        seq = _get_next_sequence(session_id, res.repo_root if res else None) if session_id else None
        return create_trace(
            "ai", ".shell-history",
            model=_claude_model(d, res.repo_root if res else None),
            transcript=transcript_path_from_hook(d),
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
        meta_extra: dict = {"is_creation": not existed}
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
        model=_claude_model(d, res.repo_root if res else None),
        range_positions=rp,
        range_contents=rc,
        transcript=transcript_path_from_hook(d),
        metadata=metadata,
        edit_sequence=seq,
        anchor_path=anchor,
        resolution=res,
    ), "PostToolUse"


def _SessionStart(d):
    from ..trace import create_trace, resolve_file_project

    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    repo = res.repo_root if res else None
    m = d.get("model") or d.get("model_id")
    if d.get("session_id") and m and repo:
        _remember_session_model(str(d["session_id"]), str(m), repo)
    return create_trace(
        "ai", ".sessions",
        model=_claude_model(d, repo),
        metadata={
            "event": "session_start",
            "session_id": d.get("session_id"),
            "source": d.get("source"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "SessionStart"


def _SessionEnd(d):
    from ..trace import create_trace, resolve_file_project

    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    repo = res.repo_root if res else None
    return create_trace(
        "ai", ".sessions",
        model=_claude_model(d, repo),
        metadata={
            "event": "session_end",
            "session_id": d.get("session_id"),
            "reason": d.get("reason"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "SessionEnd"


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------

class ClaudeAdapter(CodingAgentAdapter):
    name = "claude"
    display_name = "Claude Code"
    rules_dir = ".claude/rules"
    rule_extension = ".md"

    EVENTS = {
        "PostToolUse": _PostToolUse,
        "SessionStart": _SessionStart,
        "SessionEnd": _SessionEnd,
    }

    # Claude's ``Stop`` (and lowercase ``stop``) is "the agent finished
    # responding". No trace, but fires the summary command — and we use
    # ``pre_summary_hook`` to refresh the model cache from the transcript
    # in case ``/model`` changed mid-session.
    summary_only_events = ("Stop", "stop")

    project_dir_env_vars = ("CLAUDE_PROJECT_DIR",)

    def global_config_path(self) -> Path:
        return CLAUDE_GLOBAL_SETTINGS_FILE

    def project_config_paths(self) -> tuple[str, ...]:
        return (CLAUDE_SETTINGS_FILE,)

    def detect_tool_info(self) -> dict | None:
        if os.environ.get("CLAUDE_PROJECT_DIR"):
            return {"name": "claude-code"}
        return None

    def pre_summary_hook(self, data: dict) -> None:
        """Refresh the per-session model cache from the transcript tail.

        Runs before summary on ``Stop`` / ``stop`` so ``/model`` changes
        that happened mid-session are reflected on subsequent traces.
        """
        from ..record import project_dir_from_hook, transcript_path_from_hook
        from ..trace import resolve_file_project

        sid = data.get("session_id")
        tp = transcript_path_from_hook(data)
        if not sid or not tp:
            return
        anchor = project_dir_from_hook(data)
        res = resolve_file_project(".sessions", anchor_path=anchor)
        repo = res.repo_root if res else None
        if not repo:
            return
        m = _model_from_transcript_tail(tp)
        if not m:
            return
        prev = _session_model_from_state(str(sid), repo)
        if prev != m:
            _remember_session_model(str(sid), m, repo)

    def inject(
        self,
        record_invocation: str,
        project_dir: str | None = None,
        *,
        global_install: bool = False,
    ) -> bool:
        if global_install:
            settings_path = CLAUDE_GLOBAL_SETTINGS_FILE
        else:
            if project_dir is None:
                project_dir = os.getcwd()
            settings_path = Path(project_dir) / CLAUDE_SETTINGS_FILE

        settings_path.parent.mkdir(parents=True, exist_ok=True)

        if settings_path.exists():
            try:
                config = json.loads(settings_path.read_text())
            except (json.JSONDecodeError, OSError):
                config = {}
        else:
            config = {}

        config.setdefault("hooks", {})

        hook_entry = {"type": "command", "command": record_invocation}

        for event in ("SessionStart", "SessionEnd"):
            existing = config["hooks"].get(event, [])
            already = any(
                any(record_invocation in h.get("command", "") for h in entry.get("hooks", []))
                for entry in existing
                if isinstance(entry, dict)
            )
            if not already:
                existing.append({"hooks": [hook_entry]})
                config["hooks"][event] = existing

        post = config["hooks"].get("PostToolUse", [])
        already = any(
            any(record_invocation in h.get("command", "") for h in entry.get("hooks", []))
            for entry in post
            if isinstance(entry, dict)
        )
        if not already:
            config["hooks"]["PostToolUse"] = [
                {"matcher": "Write|Edit", "hooks": [hook_entry]},
                {"matcher": "Bash", "hooks": [hook_entry]},
            ]

        stop = config["hooks"].get("Stop", [])
        already = any(
            any(record_invocation in h.get("command", "") for h in entry.get("hooks", []))
            for entry in stop
            if isinstance(entry, dict)
        )
        if not already:
            stop.append({"hooks": [hook_entry]})
            config["hooks"]["Stop"] = stop

        settings_path.write_text(json.dumps(config, indent=2) + "\n")
        return True

    def remove(self) -> bool:
        return _remove_agent_trace_from_claude(CLAUDE_GLOBAL_SETTINGS_FILE)

    def is_installed(self, *, global_only: bool = True) -> bool:
        try:
            raw = CLAUDE_GLOBAL_SETTINGS_FILE.read_text()
        except (OSError, FileNotFoundError):
            return False
        return AGENT_TRACE_CMD in raw


def _remove_agent_trace_from_claude(settings_path: Path) -> bool:
    if not settings_path.is_file():
        return False
    try:
        config = json.loads(settings_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return False

    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue
        filtered = []
        for entry in entries:
            if not isinstance(entry, dict):
                filtered.append(entry)
                continue
            inner = entry.get("hooks", [])
            if isinstance(inner, list) and any(
                AGENT_TRACE_CMD in h.get("command", "")
                for h in inner if isinstance(h, dict)
            ):
                changed = True
                continue
            filtered.append(entry)
        if len(filtered) != len(entries):
            changed = True
        if filtered:
            hooks[event] = filtered
        else:
            del hooks[event]

    if changed:
        if not hooks:
            del config["hooks"]
        settings_path.write_text(json.dumps(config, indent=2) + "\n")
    return changed
