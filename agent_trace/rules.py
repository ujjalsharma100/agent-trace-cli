"""
Rule management for coding agents.

Prebuilt rules that teach coding agents (Cursor, Claude Code) how to
use agent-trace features.  Each rule is identified by a short name
and can be added/removed independently per tool.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


# ===================================================================
# Rule file locations
# ===================================================================

CURSOR_RULES_DIR = ".cursor/rules"
CLAUDE_RULES_DIR = ".claude/rules"

TOOL_CHOICES = ("cursor", "claude")


def _rule_path(rule_name: str, tool: str, project_dir: str | None = None) -> Path:
    """Return the file path for a rule given its name and tool."""
    if project_dir is None:
        project_dir = os.getcwd()
    if tool == "cursor":
        return Path(project_dir) / CURSOR_RULES_DIR / f"agent-trace-{rule_name}.mdc"
    elif tool == "claude":
        return Path(project_dir) / CLAUDE_RULES_DIR / f"agent-trace-{rule_name}.md"
    else:
        raise ValueError(f"Unknown tool: {tool}")


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

This returns attribution segments with metadata for each line range: whether it was AI or human authored, which model/tool wrote it, and crucially the `conversation_size` (character count, line count, turn count) and a short `preview` of the conversation.

If there are no AI-attributed segments, or the preview gives you enough context, stop here.

## Step 2: Decide how to load the full conversation

Look at `conversation_size` for each AI-attributed segment:

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

## When to use this

- When you're about to modify AI-generated code and want to understand the original intent
- When you encounter code whose purpose or design isn't clear from the code itself
- When the user asks about why code was written a certain way
- You do NOT need to fetch context for every file you read — use judgment
"""

_CONTEXT_FOR_AGENTS_CURSOR = """\
---
description: agent-trace context retrieval for AI-attributed code
alwaysApply: true
---

""" + _CONTEXT_FOR_AGENTS_BODY

_CONTEXT_FOR_AGENTS_CLAUDE = _CONTEXT_FOR_AGENTS_BODY


# ===================================================================
# Rule registry
# ===================================================================

AVAILABLE_RULES: dict[str, dict[str, Any]] = {
    "context-for-agents": {
        "description": _CONTEXT_FOR_AGENTS_DESCRIPTION,
        "cursor": _CONTEXT_FOR_AGENTS_CURSOR,
        "claude": _CONTEXT_FOR_AGENTS_CLAUDE,
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

    if tool not in TOOL_CHOICES:
        print(f"Unknown tool: {tool}", file=sys.stderr)
        print(f"Available tools: {', '.join(TOOL_CHOICES)}", file=sys.stderr)
        sys.exit(1)

    rule_def = AVAILABLE_RULES[rule_name]
    content = rule_def[tool]
    path = _rule_path(rule_name, tool, project_dir)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


def remove_rule(rule_name: str, tool: str, project_dir: str | None = None) -> str | None:
    """Remove a rule file. Returns the path removed, or None if not found."""
    if tool not in TOOL_CHOICES:
        print(f"Unknown tool: {tool}", file=sys.stderr)
        print(f"Available tools: {', '.join(TOOL_CHOICES)}", file=sys.stderr)
        sys.exit(1)

    path = _rule_path(rule_name, tool, project_dir)
    if path.exists():
        path.unlink()
        return str(path)
    return None


def show_rules(project_dir: str | None = None) -> list[dict[str, str]]:
    """Scan for active agent-trace rules. Returns list of {name, tool, path}."""
    if project_dir is None:
        project_dir = os.getcwd()

    active: list[dict[str, str]] = []

    for rule_name in AVAILABLE_RULES:
        for tool in TOOL_CHOICES:
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
