"""
Context retrieval for coding agents.

Retrieves AI attribution metadata and conversation context for a file
(or line range).  Two modes:

  - **Default:** Attribution segments with metadata, conversation size
    stats, and a short preview (~200 chars).  Light enough to inline
    in an agent's context window.

  - **Full (--full):** Everything from default mode plus the complete
    conversation transcript for each AI-attributed segment.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .blame import blame_file
from .conversations import cache_path_for_sha, latest_sha_for_conversation
from .storage import resolve_project_id
from .summary import latest_summary_by_id


# ===================================================================
# Conversation content helpers
# ===================================================================

def _resolve_conversation_from_cache(
    project_id: str, content_sha256: str,
) -> str | None:
    """Read full conversation bytes from the per-project cache."""
    if not project_id or not content_sha256:
        return None
    p = cache_path_for_sha(project_id, content_sha256)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, IOError):
        return None


def _content_sha_for_conversation_id(
    project_id: str, conversation_id: str,
) -> str | None:
    """Latest cached ``content_sha256`` for a given ``conversation_id``."""
    if not project_id or not conversation_id:
        return None
    return latest_sha_for_conversation(project_id, conversation_id)


def _compute_conversation_stats(content: str) -> dict[str, int]:
    """Compute size statistics for a conversation transcript."""
    lines = content.split("\n")
    # Count turns heuristically: lines starting with common role prefixes
    turn_prefixes = ("User:", "Human:", "Assistant:", "AI:", "System:",
                     "user:", "human:", "assistant:", "ai:", "system:",
                     "**User", "**Human", "**Assistant", "**AI",
                     "## User", "## Human", "## Assistant", "## AI",
                     "### User", "### Human", "### Assistant", "### AI")
    turns = 0
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(p) for p in turn_prefixes):
            turns += 1

    # If no structured turns detected, estimate from content blocks
    if turns == 0:
        # Try JSON-style conversation (array of messages)
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                turns = len(parsed)
            elif isinstance(parsed, dict) and "messages" in parsed:
                turns = len(parsed["messages"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "characters": len(content),
        "lines": len(lines),
        "turns": max(turns, 1),  # At least 1 if content exists
    }


def _extract_preview(content: str, max_chars: int = 200) -> str:
    """Extract the first ~max_chars of conversation content as a preview."""
    content = content.strip()
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "..."


# ===================================================================
# Core context pipeline
# ===================================================================

def get_context(
    file_path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    full: bool = False,
    query: str | None = None,
    project_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Run the context pipeline: blame → resolve conversations → build segments.

    Returns a list of context segments, each with attribution metadata
    and conversation info.
    """
    cwd = project_dir or os.getcwd()

    pid = resolve_project_id(cwd, create=False)
    summary_lookup = latest_summary_by_id(pid) if pid else {}

    # Run blame in JSON mode to get structured attribution data
    blame_json = blame_file(
        file_path,
        start_line=start_line,
        end_line=end_line,
        json_output=True,
        show_no_attribution=True,
        project_dir=cwd,
    )

    if blame_json is None:
        return []

    try:
        blame_data = json.loads(blame_json)
    except json.JSONDecodeError:
        return []

    attributions = blame_data.get("attributions", [])
    if not attributions:
        return []

    # Build context segments from attributions
    segments: list[dict[str, Any]] = []

    for attr in attributions:
        attr_start = attr.get("start_line", 0)
        attr_end = attr.get("end_line", 0)

        # Determine attribution type (deterministic blame: kind + ledger labels)
        kind = attr.get("kind", "")
        attribution_label = attr.get("attribution_label", "")
        contributor_type = attr.get("contributor_type", "")

        is_ai = (
            kind == "AI"
            or attribution_label == "AI"
            or contributor_type == "ai"
        )

        if not is_ai:
            # Anything that isn't a verified AI match is NO_ATTRIBUTION.
            segments.append({
                "start_line": attr_start,
                "end_line": attr_end,
                "attribution": "no_attribution",
            })
            continue

        # AI attribution — resolve conversation context
        segment: dict[str, Any] = {
            "start_line": attr_start,
            "end_line": attr_end,
            "attribution": "ai",
        }

        # Attribution metadata
        model_id = attr.get("model_id")
        if model_id:
            segment["model_id"] = model_id

        tool = attr.get("tool")
        if tool:
            if isinstance(tool, dict):
                segment["tool"] = tool.get("name", "")
            else:
                segment["tool"] = str(tool)

        trace_id = attr.get("trace_id")
        if trace_id:
            segment["trace_id"] = trace_id

        confidence = attr.get("confidence", 0.0)
        segment["confidence"] = confidence

        conversation_id = attr.get("conversation_id")
        if conversation_id:
            segment["conversation_id"] = conversation_id

        # Pluggable summary (id-keyed) takes precedence over the raw preview.
        summary_text = summary_lookup.get(conversation_id) if conversation_id else None
        if summary_text:
            segment["summary"] = summary_text

        # Try to resolve conversation content from the local content-addressed cache.
        conversation_content = None
        if conversation_id and pid:
            sha = _content_sha_for_conversation_id(pid, conversation_id)
            if sha:
                conversation_content = _resolve_conversation_from_cache(pid, sha)

        if conversation_content:
            # Compute size stats
            segment["conversation_size"] = _compute_conversation_stats(
                conversation_content,
            )
            # Preview is only useful as a fallback when no summary exists.
            if not summary_text:
                segment["preview"] = _extract_preview(conversation_content)

            # Include full content only when requested
            if full:
                segment["conversation_content"] = conversation_content
        else:
            # No content available — still include URL if present
            segment["conversation_size"] = None
            if not summary_text:
                segment["preview"] = None

        # Pass through query for subagent instruction forwarding
        if query:
            segment["query"] = query

        segments.append(segment)

    return segments


# ===================================================================
# Output formatting
# ===================================================================

# ANSI colour codes
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def format_text(file_path: str, segments: list[dict[str, Any]], full: bool = False) -> str:
    """Format context segments as human-readable text."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {_BOLD}{file_path}{_RESET}")
    lines.append("")

    for seg in segments:
        start = seg.get("start_line", 0)
        end = seg.get("end_line", 0)
        attribution = seg.get("attribution", "no_attribution")

        if start == end:
            lr = f"L{start}"
        else:
            lr = f"L{start}-{end}"

        if attribution != "ai":
            lines.append(f"  {lr:<14}{_DIM}No attribution{_RESET}")
            continue

        model_id = seg.get("model_id", "")
        tool = seg.get("tool", "")

        model_tool = model_id
        if tool:
            model_tool = f"{model_id} via {tool}" if model_id else tool

        lines.append(f"  {lr:<14}{_GREEN}AI{_RESET} ({model_tool})")

        summary = seg.get("summary")
        if summary:
            lines.append(f"                Summary: {summary}")

        # Conversation size — only useful alongside the raw preview fallback.
        conv_size = seg.get("conversation_size")
        if conv_size and not summary:
            chars = conv_size["characters"]
            conv_lines = conv_size["lines"]
            turns = conv_size["turns"]
            lines.append(
                f"                {_DIM}Conversation: {chars:,} chars, "
                f"{conv_lines:,} lines, {turns} turns{_RESET}"
            )

        # Preview — only when no generated summary is available.
        preview = seg.get("preview")
        if preview and not summary:
            preview_line = preview.replace("\n", " ").strip()
            if len(preview_line) > 120:
                preview_line = preview_line[:120] + "..."
            lines.append(f"                Preview: \"{preview_line}\"")

        # Full content
        if full and seg.get("conversation_content"):
            lines.append(f"                {_CYAN}--- Full transcript ---{_RESET}")
            for content_line in seg["conversation_content"].split("\n"):
                lines.append(f"                {content_line}")
            lines.append(f"                {_CYAN}--- End transcript ---{_RESET}")

        # Hint for full retrieval
        if not full and conv_size:
            lines.append(
                f"                Full transcript: "
                f"agent-trace context {file_path} --lines {start}-{end} --full"
            )

        # Query passthrough
        if seg.get("query"):
            lines.append(f"                {_DIM}Query: {seg['query']}{_RESET}")

    lines.append("")
    return "\n".join(lines)


def format_json(
    file_path: str,
    segments: list[dict[str, Any]],
) -> str:
    """Format context segments as JSON."""
    output = {
        "file": file_path,
        "segments": segments,
    }
    return json.dumps(output, indent=2)


# ===================================================================
# CLI entry point
# ===================================================================

def context_command(
    file_path: str,
    *,
    lines_range: str | None = None,
    full: bool = False,
    json_output: bool = False,
    query: str | None = None,
    project_dir: str | None = None,
) -> None:
    """Execute the context command (called from cli.py)."""
    # Parse --lines range
    start_line = None
    end_line = None
    if lines_range:
        parts = lines_range.split("-", 1)
        try:
            start_line = int(parts[0])
            end_line = int(parts[1]) if len(parts) > 1 else start_line
        except (ValueError, IndexError):
            print(f"Invalid lines range: {lines_range}  (expected format: START-END)",
                  file=sys.stderr)
            sys.exit(1)

    segments = get_context(
        file_path,
        start_line=start_line,
        end_line=end_line,
        full=full,
        query=query,
        project_dir=project_dir,
    )

    if not segments:
        if json_output:
            print(format_json(file_path, []))
        else:
            print(f"\n  No attribution data found for {file_path}\n")
        return

    if json_output:
        print(format_json(file_path, segments))
    else:
        print(format_text(file_path, segments, full=full))
