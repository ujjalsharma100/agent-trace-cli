"""
Trace record construction helpers.

Builds trace records from hook event data following the agent-trace spec.
No external dependencies — stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone


# -------------------------------------------------------------------
# Environment helpers
# -------------------------------------------------------------------

def get_workspace_root() -> str:
    """Detect the workspace / project root directory."""
    for env_var in ("CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        val = os.environ.get(env_var)
        if val:
            return val
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def get_tool_info() -> dict:
    """Detect which AI coding tool invoked the hook."""
    cursor_ver = os.environ.get("CURSOR_VERSION")
    if cursor_ver:
        return {"name": "cursor", "version": cursor_ver}
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return {"name": "claude-code"}
    return {"name": "unknown"}


def get_vcs_info(cwd: str | None = None) -> dict | None:
    """Current git revision, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        if result.returncode == 0:
            return {"type": "git", "revision": result.stdout.strip()}
    except Exception:
        pass
    return None


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------

def to_relative_path(absolute_path: str, root: str) -> str:
    try:
        return os.path.relpath(absolute_path, root)
    except ValueError:
        return absolute_path


def normalize_model_id(model: str | None) -> str | None:
    """Add provider prefix to bare model names."""
    if not model:
        return None
    if "/" in model:
        return model
    prefixes = {
        "claude-": "anthropic",
        "gpt-": "openai",
        "o1": "openai",
        "o3": "openai",
        "gemini-": "google",
    }
    for prefix, provider in prefixes.items():
        if model.startswith(prefix):
            return f"{provider}/{model}"
    return model


def compute_content_hash(content: str) -> str:
    """SHA-256 hash (truncated) for dedup / verification.

    Uses 16 hex chars (64 bits) — collision-safe for any realistic project.
    Backward-compatible: old 8-char hashes still work with prefix matching.

    Normalization: CRLF/CR → LF, and trailing newline stripped so that the
    same logical content hashes identically whether stored (e.g. tool
    new_string with trailing \\n) or matched (e.g. \"\\n\".join(blame lines)).
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{h}"


def compute_range_positions(
    edits: list[dict],
    file_content: str | None = None,
) -> list[dict]:
    """Derive line-range positions from a list of edits."""
    positions: list[dict] = []
    for edit in edits:
        new_string = edit.get("new_string", "")
        if not new_string:
            continue

        rng = edit.get("range")
        if rng:
            positions.append({
                "start_line": rng.get("start_line_number", 1),
                "end_line": rng.get("end_line_number", 1),
            })
        elif file_content:
            idx = file_content.find(new_string)
            line_count = new_string.count("\n") + 1
            if idx != -1:
                start = file_content[:idx].count("\n") + 1
                positions.append({"start_line": start, "end_line": start + line_count - 1})
            else:
                positions.append({"start_line": 1, "end_line": line_count})
        else:
            line_count = new_string.count("\n") + 1
            positions.append({"start_line": 1, "end_line": line_count})
    return positions


# -------------------------------------------------------------------
# Trace construction
# -------------------------------------------------------------------

def create_trace(
    contributor_type: str,
    file_path: str,
    *,
    model: str | None = None,
    range_positions: list[dict] | None = None,
    range_contents: list[str] | None = None,
    transcript: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build a trace record dict."""
    root = get_workspace_root()
    model_id = normalize_model_id(model)
    conversation_url = f"file://{transcript}" if transcript else None

    # Build ranges
    ranges: list[dict] = []
    if range_positions:
        for i, pos in enumerate(range_positions):
            r = {"start_line": pos["start_line"], "end_line": pos["end_line"]}
            if range_contents and i < len(range_contents) and range_contents[i]:
                r["content_hash"] = compute_content_hash(range_contents[i])
            ranges.append(r)
    if not ranges:
        ranges = [{"start_line": 1, "end_line": 1}]

    # Conversation entry
    conversation: dict = {
        "contributor": {"type": contributor_type},
        "ranges": ranges,
    }
    if model_id:
        conversation["contributor"]["model_id"] = model_id
    if conversation_url:
        conversation["url"] = conversation_url

    trace: dict = {
        "version": "1.0",
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": get_tool_info(),
        "files": [
            {
                "path": to_relative_path(file_path, root),
                "conversations": [conversation],
            }
        ],
    }

    vcs = get_vcs_info(root)
    if vcs:
        trace["vcs"] = vcs

    if metadata:
        trace["metadata"] = {k: v for k, v in metadata.items() if v is not None}

    return trace
