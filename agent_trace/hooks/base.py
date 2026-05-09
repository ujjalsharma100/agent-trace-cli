"""
Base interface for coding-agent adapters (Cursor, Claude Code, Codex, ...).

An ``Adapter`` is the single place where everything about one coding agent
lives:

  - ``inject`` / ``remove`` / ``is_installed`` — manage the agent's hook
    config (project- or global-level).
  - ``EVENTS`` — map of hook event names this agent emits to translator
    functions that build canonical trace records.
  - ``rules_dir`` / ``rule_extension`` — where prebuilt rules go for this
    agent.
  - ``detect_tool_info`` — recognise the agent at runtime via env/files
    so traces carry the right ``tool.name`` / ``tool.version``.

To add support for a new coding agent, subclass ``CodingAgentAdapter`` in
a new file under ``agent_trace/hooks/<agent>.py`` and register it via
``register_adapter`` in ``agent_trace/hooks/__init__.py``. Nothing else
in the codebase should hardcode the agent's name.

Stdlib only — no external dependencies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Protocol


AGENT_TRACE_CMD = "agent-trace record"


# Trace handler: takes raw hook JSON, returns (trace_dict_or_None, event_name).
EventHandler = Callable[[dict], "tuple[dict | None, str]"]


class Adapter(Protocol):
    """Structural protocol for back-compat with earlier callers."""

    name: str

    def global_config_path(self) -> Path: ...

    def inject(
        self,
        record_invocation: str,
        project_dir: str | None = None,
        *,
        global_install: bool = False,
    ) -> bool: ...

    def remove(self) -> bool: ...

    def is_installed(self) -> bool: ...


class CodingAgentAdapter(ABC):
    """Abstract base for a coding-agent integration.

    Subclasses must set ``name`` (the registry key) and implement the
    hook-injection methods. The remaining pieces (event handlers, rules
    dir, tool detection) are *optional* but encouraged so that every
    surface — ``record``, ``doctor``, ``rule``, ``status`` — can pick
    them up dynamically.
    """

    # Required: short slug used by CLI flags (--tool <name>) and registry.
    name: str = ""

    # Optional: human label printed in doctor / status output.
    display_name: str = ""

    # Optional: hook event names this adapter emits → translator functions.
    # Translators return ``(trace_dict_or_None, event_name)``. If empty,
    # the dispatcher in ``record.py`` will not route stdin events to this
    # adapter (e.g. for an adapter that only manages config, no recording).
    EVENTS: dict[str, EventHandler] = {}

    # Optional: where prebuilt rules live for this agent (relative to repo).
    rules_dir: str = ""

    # Optional: file extension for rule files (".md", ".mdc", ...).
    rule_extension: str = ".md"

    # Optional: hook event names that signal "the agent's turn ended" — the
    # dispatcher fires the summary command for these and does NOT route the
    # event to a trace handler. (e.g. Claude's ``Stop``, Cursor's
    # ``afterAgentResponse``.)
    summary_only_events: tuple[str, ...] = ()

    # Optional: hook event names that should fire summary AND ALSO be
    # dispatched as a regular trace event (e.g. Cursor's ``sessionEnd``,
    # which both ends the session and emits a session-end trace).
    summary_then_trace_events: tuple[str, ...] = ()

    # Optional: env var names where this agent puts the transcript path.
    # ``transcript_path_from_hook`` walks the registry's union of these.
    # (e.g. Cursor's ``CURSOR_TRANSCRIPT_PATH``.)
    transcript_env_vars: tuple[str, ...] = ()

    # Optional: env var names where this agent puts the workspace dir.
    # ``project_dir_from_hook`` walks the registry's union of these.
    # (e.g. ``CURSOR_PROJECT_DIR``, ``CLAUDE_PROJECT_DIR``.)
    project_dir_env_vars: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Hooks: install / remove / inspect
    # ------------------------------------------------------------------

    @abstractmethod
    def global_config_path(self) -> Path:
        """Where the global hook configuration for this agent lives."""

    @abstractmethod
    def inject(
        self,
        record_invocation: str,
        project_dir: str | None = None,
        *,
        global_install: bool = False,
    ) -> bool:
        """Idempotently install hooks that pipe events to ``record_invocation``.

        ``global_install=True`` writes the user-level config; otherwise
        writes a project-scoped config under ``project_dir`` (defaults to
        the current working directory).
        """

    @abstractmethod
    def remove(self) -> bool:
        """Remove the agent-trace entries from the *global* config.

        Returns ``True`` if any entries were removed.
        """

    def project_config_paths(self) -> tuple[str, ...]:
        """Project-scoped config paths (relative to repo root).

        Used by ``doctor`` / ``status`` to detect project-level installs
        without hardcoding any agent's path. Default: empty.
        """
        return ()

    def is_installed(self, *, global_only: bool = True) -> bool:
        """Whether agent-trace hooks are present in this adapter's config.

        Default implementation: read the global config file and look for
        ``agent-trace record``. Override for adapters with non-text
        config (e.g. binary plist).
        """
        path = self.global_config_path()
        try:
            raw = path.read_text()
        except (OSError, FileNotFoundError):
            return False
        return AGENT_TRACE_CMD in raw

    # ------------------------------------------------------------------
    # Recording: hook event → canonical trace record
    # ------------------------------------------------------------------

    def handle_event(self, data: dict) -> tuple[dict | None, str | None]:
        """Translate a hook event payload into a trace record.

        Looks up ``data['hook_event_name']`` in ``self.EVENTS``. Returns
        ``(None, None)`` if the event is not owned by this adapter.
        """
        event = data.get("hook_event_name", "") or ""
        handler = self.EVENTS.get(event)
        if handler is None:
            return None, None
        return handler(data)

    def owns_event(self, event_name: str) -> bool:
        return event_name in self.EVENTS

    # ------------------------------------------------------------------
    # Tool identity
    # ------------------------------------------------------------------

    def detect_tool_info(self) -> dict | None:
        """Return ``{'name': ..., 'version': ...}`` if this agent is
        currently running (env vars, marker files, etc.); else ``None``.

        Default: not detectable. Override for adapters that expose a
        runtime marker.
        """
        return None

    # ------------------------------------------------------------------
    # Rule files (prebuilt rules dropped under the project tree)
    # ------------------------------------------------------------------

    def rule_path(self, rule_name: str, project_dir: str | None = None) -> Path:
        """Return the file path for a rule under this agent's rules dir."""
        import os

        if not self.rules_dir:
            raise NotImplementedError(
                f"Adapter {self.name!r} does not declare a rules_dir; "
                "set ``rules_dir`` on the subclass to enable rules."
            )
        if project_dir is None:
            project_dir = os.getcwd()
        return Path(project_dir) / self.rules_dir / f"agent-trace-{rule_name}{self.rule_extension}"

    def supports_rules(self) -> bool:
        return bool(self.rules_dir)

    # ------------------------------------------------------------------
    # Session lifecycle / summary integration
    # ------------------------------------------------------------------

    def pre_summary_hook(self, data: dict) -> None:
        """Adapter-specific work to run *before* a summary fires.

        Default: no-op. Claude overrides this to refresh its session
        model cache from the transcript, since ``PostToolUse`` payloads
        do not include a model id.
        """
        return None

    def is_session_end(self, event_name: str) -> tuple[bool, bool]:
        """Classify an event for the session-end / summary pipeline.

        Returns ``(triggers_summary, also_dispatch_handler)``.
        """
        if event_name in self.summary_only_events:
            return True, False
        if event_name in self.summary_then_trace_events:
            return True, True
        return False, False
