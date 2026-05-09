"""Coding-agent adapters and the registry that drives them.

To add a new agent:

  1. Create ``agent_trace/hooks/<agent>.py`` with a class that subclasses
     ``CodingAgentAdapter`` (see ``base.py`` for the contract).
  2. Implement ``inject`` / ``remove`` / ``global_config_path`` for the
     agent's hook surface.
  3. Optionally declare ``EVENTS`` (event-name → translator), a
     ``rules_dir``/``rule_extension`` and a ``detect_tool_info``.
  4. Register the adapter at the bottom of this file with
     ``register_adapter(NewAdapter())``.

Nothing else in the codebase should hardcode the agent's name —
``cli.py``, ``record.py``, ``rules.py`` and ``trace.get_tool_info``
all walk the registry.
"""

from __future__ import annotations

from .base import AGENT_TRACE_CMD, Adapter, CodingAgentAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .cursor import CursorAdapter

_ADAPTERS: dict[str, CodingAgentAdapter] = {}


def register_adapter(adapter: CodingAgentAdapter) -> None:
    _ADAPTERS[adapter.name] = adapter


def iter_adapters() -> list[CodingAgentAdapter]:
    return list(_ADAPTERS.values())


def get_adapter(name: str) -> CodingAgentAdapter | None:
    return _ADAPTERS.get(name)


def adapter_names() -> list[str]:
    return list(_ADAPTERS)


# Built-in adapters. Order matters for CLI choices and doctor output.
register_adapter(CursorAdapter())
register_adapter(ClaudeAdapter())
register_adapter(CodexAdapter())


# ---------------------------------------------------------------------
# Back-compat tool-specific shims (cursor / claude). Kept for callers
# that still import the named functions; new code should iterate the
# registry via ``iter_adapters`` / ``setup_global_hooks``.
# ---------------------------------------------------------------------


def configure_cursor_hooks(project_dir: str | None = None, *, global_install: bool = False) -> bool:
    return _ADAPTERS["cursor"].inject(
        AGENT_TRACE_CMD,
        project_dir=project_dir,
        global_install=global_install,
    )


def configure_claude_hooks(project_dir: str | None = None, *, global_install: bool = False) -> bool:
    return _ADAPTERS["claude"].inject(
        AGENT_TRACE_CMD,
        project_dir=project_dir,
        global_install=global_install,
    )


def has_global_cursor_hooks() -> bool:
    return _ADAPTERS["cursor"].is_installed()


def has_global_claude_hooks() -> bool:
    return _ADAPTERS["claude"].is_installed()


def has_global_hooks(tool: str | None = None) -> bool:
    if tool is None:
        return any(adapter.is_installed() for adapter in iter_adapters())
    adapter = _ADAPTERS.get(tool)
    return adapter.is_installed() if adapter else False


def remove_global_cursor_hooks() -> bool:
    return _ADAPTERS["cursor"].remove()


def remove_global_claude_hooks() -> bool:
    return _ADAPTERS["claude"].remove()


def configure_project_hooks(
    project_dir: str | None = None,
    tools: list[str] | None = None,
) -> dict[str, bool]:
    """Run ``inject`` (project-level) for every adapter or a subset.

    Replaces the per-tool ``configure_*_hooks`` calls in ``init`` so new
    adapters automatically participate.
    """
    if tools is None:
        tools = list(_ADAPTERS)
    out: dict[str, bool] = {}
    for tool in tools:
        adapter = _ADAPTERS.get(tool)
        if adapter is None:
            continue
        out[tool] = adapter.inject(
            AGENT_TRACE_CMD,
            project_dir=project_dir,
            global_install=False,
        )
    return out


def setup_global_hooks(tools: list[str] | None = None) -> dict[str, bool]:
    if tools is None:
        tools = list(_ADAPTERS)
    results: dict[str, bool] = {}
    for tool in tools:
        adapter = _ADAPTERS.get(tool)
        if adapter is None:
            continue
        results[tool] = adapter.inject(AGENT_TRACE_CMD, global_install=True)
    return results


def remove_global_hooks(tools: list[str] | None = None) -> dict[str, bool]:
    if tools is None:
        tools = list(_ADAPTERS)
    results: dict[str, bool] = {}
    for tool in tools:
        adapter = _ADAPTERS.get(tool)
        if adapter is None:
            continue
        results[tool] = adapter.remove()
    return results


__all__ = [
    "AGENT_TRACE_CMD",
    "Adapter",
    "CodingAgentAdapter",
    "register_adapter",
    "iter_adapters",
    "get_adapter",
    "adapter_names",
    "setup_global_hooks",
    "remove_global_hooks",
    "configure_project_hooks",
    "configure_cursor_hooks",
    "configure_claude_hooks",
    "has_global_cursor_hooks",
    "has_global_claude_hooks",
    "has_global_hooks",
    "remove_global_cursor_hooks",
    "remove_global_claude_hooks",
]
