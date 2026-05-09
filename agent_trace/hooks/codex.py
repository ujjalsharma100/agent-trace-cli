"""
Codex CLI adapter — hooks injection, event translators, summary
lifecycle.

OpenAI's Codex CLI exposes a single hook surface called ``notify``: a
program declared in ``~/.codex/config.toml`` which Codex spawns when an
agent turn completes. The program receives a JSON payload as its last
argv, e.g.::

    {"type": "agent-turn-complete", "turn-id": "...",
     "input-messages": [...], "last-assistant-message": "..."}

Strategy:

  - ``inject`` writes a ``notify`` entry that pipes Codex's JSON through
    a tiny shell wrapper into ``agent-trace record``. The wrapper also
    annotates the event with a canonical ``hook_event_name`` so the
    record dispatcher knows which adapter owns it.
  - The canonical event names this adapter emits:
        * ``CodexTurnComplete`` — one trace per finished agent turn
        * ``CodexSessionStart`` — set-up hook (optional, written when
          Codex's config supports it; Codex doesn't currently expose
          a session-start hook, so this event is here so tests and
          future Codex versions can drive it via stdin).
  - For tests / fixtures, simply pipe a JSON object with
    ``"hook_event_name": "CodexTurnComplete"`` into ``agent-trace
    record`` (mirrors the Cursor / Claude testing pattern).

To add a new coding agent, copy this file, swap the config-path /
shell-wrapper bits, declare your event names and translators, and
register the adapter in ``hooks/__init__.py``. The CLI, doctor, status
and rule surfaces will pick it up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import AGENT_TRACE_CMD, CodingAgentAdapter


CODEX_DIR = Path.home() / ".codex"
CODEX_CONFIG_FILE = CODEX_DIR / "config.toml"

# Shell snippet Codex's ``notify`` will invoke. It takes Codex's JSON
# payload (passed as the last argv), tags it with a ``hook_event_name``
# keyed off the payload's ``type`` field, and pipes the result into
# ``agent-trace record``.
_NOTIFY_SHELL = (
    "payload=\"$1\"; "
    "case \"$payload\" in *agent-turn-complete*) ev=CodexTurnComplete;; "
    "*) ev=CodexUnknown;; esac; "
    'printf \'%s\' "$payload" | python3 -c '
    "'import json,sys; "
    'd=json.loads(sys.stdin.read() or "{}"); '
    'd["hook_event_name"]=sys.argv[1]; '
    "print(json.dumps(d))' "
    "\"$ev\" | agent-trace record"
)

# Marker line written into config.toml when our notify entry is present.
_AGENT_TRACE_NOTIFY_MARKER = "# agent-trace:notify"
_NOTIFY_ARRAY_PREFIX = "notify = "


# ---------------------------------------------------------------------
# Event translators (hook payload → trace record)
# ---------------------------------------------------------------------

def _TurnComplete(d):
    from ..record import transcript_path_from_hook
    from ..trace import create_trace, resolve_file_project

    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    last_msg = d.get("last-assistant-message") or d.get("last_assistant_message")
    metadata = {
        "event": "turn_complete",
        "turn_id": d.get("turn-id") or d.get("turn_id"),
        "session_id": d.get("session_id") or d.get("session-id"),
        "input_messages": d.get("input-messages") or d.get("input_messages"),
        "last_assistant_message": last_msg,
    }
    return create_trace(
        "ai", ".sessions",
        model=d.get("model") or d.get("model_id"),
        transcript=transcript_path_from_hook(d),
        metadata=metadata,
        anchor_path=anchor,
        resolution=res,
    ), "CodexTurnComplete"


def _SessionStart(d):
    from ..trace import create_trace, resolve_file_project

    anchor = d.get("cwd") or os.getcwd()
    res = resolve_file_project(".sessions", anchor_path=anchor)
    return create_trace(
        "ai", ".sessions",
        model=d.get("model") or d.get("model_id"),
        metadata={
            "event": "session_start",
            "session_id": d.get("session_id") or d.get("session-id"),
            "source": d.get("source"),
        },
        anchor_path=anchor,
        resolution=res,
    ), "CodexSessionStart"


# ---------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------

class CodexAdapter(CodingAgentAdapter):
    name = "codex"
    display_name = "Codex CLI"
    rules_dir = ".codex/rules"
    rule_extension = ".md"

    EVENTS = {
        "CodexTurnComplete": _TurnComplete,
        "CodexSessionStart": _SessionStart,
    }

    def global_config_path(self) -> Path:
        return CODEX_CONFIG_FILE

    def project_config_paths(self) -> tuple[str, ...]:
        # Codex has no project-scoped config, but we drop a marker so
        # ``doctor`` can show "project notify hook installed via Codex".
        return (".codex/agent-trace.installed",)

    def detect_tool_info(self) -> dict | None:
        ver = os.environ.get("CODEX_VERSION")
        if ver:
            return {"name": "codex", "version": ver}
        if os.environ.get("CODEX_HOME"):
            return {"name": "codex"}
        return None

    def inject(
        self,
        record_invocation: str,  # noqa: ARG002 — recorded inside _NOTIFY_SHELL
        project_dir: str | None = None,
        *,
        global_install: bool = False,
    ) -> bool:
        if not global_install:
            if project_dir is None:
                project_dir = os.getcwd()
            project_marker = Path(project_dir) / ".codex" / "agent-trace.installed"
            project_marker.parent.mkdir(parents=True, exist_ok=True)
            project_marker.write_text(
                "Codex's notify hook is user-scoped (~/.codex/config.toml).\n"
                "Run: agent-trace hooks setup-global --tool codex\n",
            )
            return True

        CODEX_DIR.mkdir(parents=True, exist_ok=True)
        existing = ""
        if CODEX_CONFIG_FILE.exists():
            try:
                existing = CODEX_CONFIG_FILE.read_text()
            except OSError:
                existing = ""

        if _AGENT_TRACE_NOTIFY_MARKER in existing and AGENT_TRACE_CMD in existing:
            return True  # idempotent

        cleaned_lines: list[str] = []
        skip_next_notify = False
        for line in existing.splitlines():
            if line.strip() == _AGENT_TRACE_NOTIFY_MARKER:
                skip_next_notify = True
                continue
            if skip_next_notify and line.lstrip().startswith(_NOTIFY_ARRAY_PREFIX):
                skip_next_notify = False
                continue
            skip_next_notify = False
            cleaned_lines.append(line)

        block = (
            f"{_AGENT_TRACE_NOTIFY_MARKER}\n"
            f"notify = [\"bash\", \"-lc\", {_quote_toml(_NOTIFY_SHELL)}, \"--\"]\n"
        )
        if cleaned_lines and cleaned_lines[-1].strip():
            cleaned_lines.append("")
        cleaned_lines.append(block.rstrip())
        CODEX_CONFIG_FILE.write_text("\n".join(cleaned_lines) + "\n")
        return True

    def remove(self) -> bool:
        if not CODEX_CONFIG_FILE.is_file():
            return False
        try:
            text = CODEX_CONFIG_FILE.read_text()
        except OSError:
            return False
        if _AGENT_TRACE_NOTIFY_MARKER not in text:
            return False
        new_lines: list[str] = []
        skip_next_notify = False
        removed = False
        for line in text.splitlines():
            if line.strip() == _AGENT_TRACE_NOTIFY_MARKER:
                skip_next_notify = True
                removed = True
                continue
            if skip_next_notify and line.lstrip().startswith(_NOTIFY_ARRAY_PREFIX):
                skip_next_notify = False
                continue
            skip_next_notify = False
            new_lines.append(line)
        if removed:
            CODEX_CONFIG_FILE.write_text(("\n".join(new_lines).rstrip() + "\n") if new_lines else "")
        return removed

    def is_installed(self, *, global_only: bool = True) -> bool:
        try:
            raw = CODEX_CONFIG_FILE.read_text()
        except (OSError, FileNotFoundError):
            return False
        return _AGENT_TRACE_NOTIFY_MARKER in raw and AGENT_TRACE_CMD in raw


def _quote_toml(value: str) -> str:
    """Quote a string for inclusion in a TOML array element."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
