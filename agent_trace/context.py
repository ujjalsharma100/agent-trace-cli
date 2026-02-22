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


# ===================================================================
# Conversation content helpers
# ===================================================================

def _resolve_conversation_local(url: str) -> str | None:
    """Read full conversation content from a file:// URL."""
    if not url.startswith("file://"):
        return None
    local_path = url[7:]
    try:
        with open(local_path, "r") as f:
            return f.read()
    except (OSError, IOError):
        return None


def _resolve_conversation_remote(
    url: str,
    config: dict[str, Any],
) -> str | None:
    """Fetch conversation content from the remote service."""
    import urllib.error
    import urllib.request
    import urllib.parse

    from .config import get_auth_token, get_service_url

    project_id = config.get("project_id")
    auth_token = get_auth_token(config)
    service_url = get_service_url(config)

    if not project_id or not auth_token:
        return None

    params = urllib.parse.urlencode({"project_id": project_id, "url": url})
    req = urllib.request.Request(
        f"{service_url}/api/v1/conversations/content?{params}",
        method="GET",
    )
    req.add_header("Authorization", f"Bearer {auth_token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("content")
    except Exception:
        return None


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
    from .config import get_project_config

    cwd = project_dir or os.getcwd()

    # Run blame in JSON mode to get structured attribution data
    blame_json = blame_file(
        file_path,
        start_line=start_line,
        end_line=end_line,
        json_output=True,
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

    # Determine storage mode for remote conversation resolution
    config = get_project_config(project_dir=cwd)
    if config is None:
        config = {"storage": "local"}

    # Build context segments from attributions
    segments: list[dict[str, Any]] = []

    for attr in attributions:
        attr_start = attr.get("start_line", 0)
        attr_end = attr.get("end_line", 0)

        # Determine attribution type
        tier = attr.get("tier")
        source = attr.get("source", "")
        attribution_label = attr.get("attribution_label", "")
        contributor_type = attr.get("contributor_type", "")

        is_ai = (
            tier is not None
            or attribution_label == "AI"
            or contributor_type == "ai"
        )
        is_mixed = attribution_label == "Mixed" or contributor_type == "mixed"

        if not is_ai and not is_mixed:
            # Human or no attribution — include as a simple segment
            segments.append({
                "start_line": attr_start,
                "end_line": attr_end,
                "attribution": "human",
            })
            continue

        # AI or Mixed attribution — resolve conversation context
        segment: dict[str, Any] = {
            "start_line": attr_start,
            "end_line": attr_end,
            "attribution": "mixed" if is_mixed else "ai",
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

        conversation_url = attr.get("conversation_url")
        if conversation_url:
            segment["conversation_url"] = conversation_url

        # Try to resolve conversation content
        conversation_content = None
        if conversation_url:
            if conversation_url.startswith("file://"):
                conversation_content = _resolve_conversation_local(conversation_url)
            elif config.get("storage") == "remote":
                conversation_content = _resolve_conversation_remote(
                    conversation_url, config,
                )

        if conversation_content:
            # Compute size stats
            segment["conversation_size"] = _compute_conversation_stats(
                conversation_content,
            )
            # Always include preview
            segment["preview"] = _extract_preview(conversation_content)

            # Include full content only when requested
            if full:
                segment["conversation_content"] = conversation_content
        else:
            # No content available — still include URL if present
            segment["conversation_size"] = None
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
_YELLOW = "\033[33m"
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
        attribution = seg.get("attribution", "human")

        if start == end:
            lr = f"L{start}"
        else:
            lr = f"L{start}-{end}"

        if attribution == "human":
            lines.append(f"  {lr:<14}{_DIM}Human{_RESET}")
            continue

        # AI or Mixed
        label = "AI" if attribution == "ai" else "Mixed"
        model_id = seg.get("model_id", "")
        tool = seg.get("tool", "")

        model_tool = model_id
        if tool:
            model_tool = f"{model_id} via {tool}" if model_id else tool

        color = _GREEN if attribution == "ai" else _YELLOW
        lines.append(f"  {lr:<14}{color}{label}{_RESET} ({model_tool})")

        # Conversation size
        conv_size = seg.get("conversation_size")
        if conv_size:
            chars = conv_size["characters"]
            conv_lines = conv_size["lines"]
            turns = conv_size["turns"]
            lines.append(
                f"                {_DIM}Conversation: {chars:,} chars, "
                f"{conv_lines:,} lines, {turns} turns{_RESET}"
            )

        # Preview
        preview = seg.get("preview")
        if preview:
            # Show preview on one line, truncated
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
