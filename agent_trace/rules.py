"""
Rule management for coding agents.

Prebuilt rules teach coding agents (Cursor, Claude Code, Codex, ...)
how to use agent-trace features. Each rule is identified by a short
name and can be added/removed independently per tool.

The list of supported tools is the adapter registry (``hooks``). To
support a rule for a new agent, add the rule body under the agent's
``name`` key in ``AVAILABLE_RULES`` — the adapter itself decides where
the file lives (``rules_dir`` + ``rule_extension``).

No external dependencies — stdlib only.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .hooks import get_adapter, iter_adapters


def _supported_tools() -> tuple[str, ...]:
    """Tools that declare a ``rules_dir`` are eligible for rules."""
    return tuple(a.name for a in iter_adapters() if a.supports_rules())


# Exported for argparse ``choices=`` and other callers. Computed lazily
# so registering a new adapter is reflected without restarting Python.
class _ToolChoices:
    def __iter__(self):
        return iter(_supported_tools())

    def __contains__(self, item):
        return item in _supported_tools()

    def __getitem__(self, idx):
        return _supported_tools()[idx]

    def __len__(self):
        return len(_supported_tools())

    def __repr__(self):
        return f"TOOL_CHOICES({_supported_tools()!r})"


TOOL_CHOICES = _ToolChoices()


# ===================================================================
# Rule paths (delegated to the adapter)
# ===================================================================

def _rule_path(rule_name: str, tool: str, project_dir: str | None = None) -> Path:
    adapter = get_adapter(tool)
    if adapter is None or not adapter.supports_rules():
        raise ValueError(f"Unknown tool or tool does not support rules: {tool}")
    return adapter.rule_path(rule_name, project_dir)


# ===================================================================
# Rule content: context-for-agents
# ===================================================================

_CONTEXT_FOR_AGENTS_DESCRIPTION = (
    "Teaches the agent to retrieve conversation context behind "
    "AI-attributed code using `agent-trace context`"
)

_CONTEXT_FOR_AGENTS_BODY = """\
# agent-trace: AI conversation context for code

This project uses agent-trace to embed conversation context into code. When you read a file that contains AI-generated code, you can retrieve the conversation that produced it.

## Step 1: Get attribution metadata (always do this first)

```
agent-trace context <file> --lines START-END --json
```

This returns attribution segments with metadata for each line range: whether it was AI or human authored, which model/tool wrote it, and (when the transcript is cached locally) `conversation_size` (character count, line count, turn count) plus a short `preview` of the transcript start.

**Session summaries:** Some projects also store a per-conversation `summary` string (from agent-trace's session-end summary pipeline). When `summary` is present it replaces `preview` and is meant as a compact stand-in for the full transcript. If `summary` (or `preview`) is enough for your task, stop here — you do **not** need `--full` or a separate summarization subagent for that segment.

If there are no AI-attributed segments, or the available `summary` / `preview` is enough, stop here.

## Step 2: When you still need the full transcript

Use this only when Step 1 did not give enough detail (no usable `summary`/`preview`, or you must verify exact wording in the raw conversation).

Look at `conversation_size` for each AI-attributed segment that still needs deeper inspection:

- **Small transcript (< 3000 characters):** Load it directly in your context. Run:
  ```
  agent-trace context <file> --lines START-END --full --json
  ```
  Read the `conversation_content` field directly.

- **Large transcript (>= 3000 characters):** Do NOT load it into your main context window — it will pollute your working context. Instead, delegate to a subagent:
  - Spawn a subagent (e.g. using the Task tool, a background agent, or whatever mechanism your host provides).
  - The subagent should run:
    ```
    agent-trace context <file> --lines START-END --full --json
    ```
  - Instruct the subagent to read the full `conversation_content` and return a focused summary. Tell the subagent what you need to know — e.g. "summarize the rationale for this implementation", "what constraints were discussed", "why was this approach chosen over alternatives".
  - Use the subagent's returned summary in your reasoning.
  - Optional: pass `--query "…"` on the same command so your question is echoed in JSON (`query` field) for the subagent.

## When to use this

- When you're about to modify AI-generated code and want to understand the original intent
- When you encounter code whose purpose or design isn't clear from the code itself
- When the user asks about why code was written a certain way
- You do NOT need to fetch context for every file you read — use judgment

## More detail

Run `agent-trace context --help` or `agent-trace help` for flags, paths, and options beyond this workflow.
"""

_CONTEXT_FOR_AGENTS_CURSOR = """\
---
description: agent-trace context retrieval for AI-attributed code
alwaysApply: true
---

""" + _CONTEXT_FOR_AGENTS_BODY

_CONTEXT_FOR_AGENTS_CLAUDE = _CONTEXT_FOR_AGENTS_BODY
_CONTEXT_FOR_AGENTS_CODEX = _CONTEXT_FOR_AGENTS_BODY


# ===================================================================
# Rule registry
# ===================================================================
#
# Each rule maps a tool ``name`` (matching an adapter) to the rule body
# for that tool. Tools that aren't keyed simply skip the rule.

AVAILABLE_RULES: dict[str, dict[str, Any]] = {
    "context-for-agents": {
        "description": _CONTEXT_FOR_AGENTS_DESCRIPTION,
        "cursor": _CONTEXT_FOR_AGENTS_CURSOR,
        "claude": _CONTEXT_FOR_AGENTS_CLAUDE,
        "codex": _CONTEXT_FOR_AGENTS_CODEX,
    },
}


# ===================================================================
# Rule operations
# ===================================================================

def add_rule(rule_name: str, tool: str, project_dir: str | None = None) -> str:
    """Write a rule file for the given tool. Returns the path written."""
    if rule_name not in AVAILABLE_RULES:
        print(f"Unknown rule: {rule_name}", file=sys.stderr)
        print(f"Available rules: {', '.join(AVAILABLE_RULES.keys())}", file=sys.stderr)
        sys.exit(1)

    if tool not in _supported_tools():
        print(f"Unknown tool: {tool}", file=sys.stderr)
        print(f"Available tools: {', '.join(_supported_tools())}", file=sys.stderr)
        sys.exit(1)

    rule_def = AVAILABLE_RULES[rule_name]
    if tool not in rule_def:
        print(f"Rule '{rule_name}' is not defined for tool '{tool}'.", file=sys.stderr)
        sys.exit(1)
    content = rule_def[tool]
    path = _rule_path(rule_name, tool, project_dir)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


def remove_rule(rule_name: str, tool: str, project_dir: str | None = None) -> str | None:
    """Remove a rule file. Returns the path removed, or None if not found."""
    if tool not in _supported_tools():
        print(f"Unknown tool: {tool}", file=sys.stderr)
        print(f"Available tools: {', '.join(_supported_tools())}", file=sys.stderr)
        sys.exit(1)

    path = _rule_path(rule_name, tool, project_dir)
    if path.exists():
        path.unlink()
        return str(path)
    return None


def show_rules(project_dir: str | None = None) -> list[dict[str, str]]:
    """Scan for active agent-trace rules. Returns list of {name, tool, path}."""
    active: list[dict[str, str]] = []

    for rule_name, rule_def in AVAILABLE_RULES.items():
        for tool in _supported_tools():
            if tool not in rule_def:
                continue
            path = _rule_path(rule_name, tool, project_dir)
            if path.exists():
                active.append({
                    "name": rule_name,
                    "tool": tool,
                    "path": str(path),
                })

    return active


def list_available_rules() -> list[dict[str, str]]:
    """List all available prebuilt rules with descriptions."""
    return [
        {"name": name, "description": rule_def["description"]}
        for name, rule_def in AVAILABLE_RULES.items()
    ]
