"""
CLI blame command — show AI attribution for file lines.

Runs ``git blame --porcelain`` locally, groups lines into segments by
commit SHA, then either:

  - **Local mode:** runs attribution against ``.agent-trace/`` JSONL files
  - **Remote mode:** POSTs segment data to the ``/api/v1/blame`` endpoint

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import get_auth_token, get_project_config, get_service_url
from .ledger import load_local_ledgers
from .trace import compute_content_hash


# ===================================================================
# Signal weights  (mirrors agent-trace-service/attribution.py)
# ===================================================================

WEIGHT_COMMIT_LINK = 40
WEIGHT_CONTENT_HASH = 30
WEIGHT_REVISION_PARENT = 15
WEIGHT_REVISION_ANCESTOR = 8
WEIGHT_RANGE_MATCH = 10
WEIGHT_RANGE_OVERLAP = 5
WEIGHT_TIMESTAMP = 5


# ===================================================================
# Git helpers
# ===================================================================

def _git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _git_blame_porcelain(
    file_path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    cwd: str | None = None,
) -> str | None:
    """Run ``git blame --porcelain`` and return raw output."""
    args = ["blame", "--porcelain"]
    if start_line is not None and end_line is not None:
        args.extend(["-L", f"{start_line},{end_line}"])
    elif start_line is not None:
        args.extend(["-L", f"{start_line},{start_line}"])
    args.append(file_path)
    return _git(*args, cwd=cwd)


def _get_parent_sha(commit_sha: str, cwd: str | None = None) -> str | None:
    """Get the parent of a commit."""
    return _git("rev-parse", f"{commit_sha}^", cwd=cwd)


def _get_commit_date(commit_sha: str, cwd: str | None = None) -> str | None:
    """Get the author date of a commit in ISO-8601 format."""
    return _git("log", "-1", "--format=%aI", commit_sha, cwd=cwd)


# ===================================================================
# Git blame porcelain parser
# ===================================================================

def _parse_blame_porcelain(raw: str) -> list[dict[str, Any]]:
    """Parse ``git blame --porcelain`` output into per-line records.

    Each record:
        {
            "commit_sha": "abc123...",
            "orig_line": int,
            "final_line": int,
            "content": "line content",
            "author": "...",
            "author_time": int,     # unix timestamp
            "summary": "...",
            "filename": "...",
        }
    """
    lines = raw.split("\n")
    records: list[dict[str, Any]] = []
    commit_info: dict[str, dict[str, Any]] = {}  # sha -> header fields

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue

        # Each blamed line starts with: <sha> <orig_line> <final_line> [<num_lines>]
        parts = line.split()
        if len(parts) < 3:
            i += 1
            continue

        sha = parts[0]
        # Verify it looks like a SHA (40 hex chars)
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            i += 1
            continue

        orig_line = int(parts[1])
        final_line = int(parts[2])
        # num_lines present only for the first line of a group
        is_first_in_group = len(parts) >= 4

        i += 1

        # If this is the first time we see this commit, parse header lines
        if sha not in commit_info:
            info: dict[str, Any] = {}
            while i < len(lines):
                hline = lines[i]
                if hline.startswith("\t"):
                    break
                if hline.startswith("author "):
                    info["author"] = hline[7:]
                elif hline.startswith("author-time "):
                    try:
                        info["author_time"] = int(hline[12:])
                    except ValueError:
                        pass
                elif hline.startswith("summary "):
                    info["summary"] = hline[8:]
                elif hline.startswith("filename "):
                    info["filename"] = hline[9:]
                i += 1
            commit_info[sha] = info
        else:
            # Subsequent lines for known commit: skip to content line
            while i < len(lines) and not lines[i].startswith("\t"):
                hline = lines[i]
                if hline.startswith("filename "):
                    commit_info[sha]["filename"] = hline[9:]
                i += 1

        # Content line (starts with \t)
        content = ""
        if i < len(lines) and lines[i].startswith("\t"):
            content = lines[i][1:]  # strip leading tab
            i += 1

        info = commit_info.get(sha, {})
        records.append({
            "commit_sha": sha,
            "orig_line": orig_line,
            "final_line": final_line,
            "content": content,
            "author": info.get("author", ""),
            "author_time": info.get("author_time"),
            "summary": info.get("summary", ""),
            "filename": info.get("filename", ""),
        })

    return records


# ===================================================================
# Segment grouping
# ===================================================================

def _group_into_segments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive blame records that share the same commit SHA.

    Returns segments:
        {
            "commit_sha": "...",
            "start_line": int,        # current (final) line number
            "end_line": int,           # current (final) line number
            "orig_start_line": int,    # original line number in the commit
            "orig_end_line": int,      # original line number in the commit
            "content_lines": ["line1", "line2", ...],
            "author": "...",
            "author_time": int | None,
            "summary": "...",
            "filename": "...",
        }

    ``orig_start_line`` / ``orig_end_line`` are the line numbers as they
    were in the file version stored by the commit.  These are needed for
    ledger lookups because the ledger records line numbers at commit time,
    while subsequent commits can shift the current (final) positions.
    """
    if not records:
        return []

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for rec in records:
        if (
            current is not None
            and current["commit_sha"] == rec["commit_sha"]
            and current["end_line"] + 1 == rec["final_line"]
        ):
            current["end_line"] = rec["final_line"]
            current["orig_end_line"] = rec["orig_line"]
            current["content_lines"].append(rec["content"])
        else:
            if current is not None:
                segments.append(current)
            current = {
                "commit_sha": rec["commit_sha"],
                "start_line": rec["final_line"],
                "end_line": rec["final_line"],
                "orig_start_line": rec["orig_line"],
                "orig_end_line": rec["orig_line"],
                "content_lines": [rec["content"]],
                "author": rec.get("author", ""),
                "author_time": rec.get("author_time"),
                "summary": rec.get("summary", ""),
                "filename": rec.get("filename", ""),
            }

    if current is not None:
        segments.append(current)

    return segments


# ===================================================================
# Content hash (single implementation: trace.compute_content_hash)
# ===================================================================

def _content_hash_for_segment(content_lines: list[str]) -> str:
    """Content hash for a blame segment; uses same normalization as trace storage."""
    return compute_content_hash("\n".join(content_lines))


# ===================================================================
# Conversation content helper
# ===================================================================

def _load_conversation_summary(url: str | None, max_chars: int = 200) -> str | None:
    """Read a file:// conversation URL and return a truncated summary."""
    if not url or not url.startswith("file://"):
        return None
    local_path = url[7:]
    try:
        with open(local_path, "r") as f:
            content = f.read(max_chars + 100)
        content = content.strip()
        if not content:
            return None
        if len(content) > max_chars:
            content = content[:max_chars] + "..."
        return content
    except (OSError, IOError):
        return None


# ===================================================================
# Local data loading
# ===================================================================

def _load_local_traces(project_dir: str) -> list[dict[str, Any]]:
    """Load all traces from .agent-trace/traces.jsonl."""
    traces_path = Path(project_dir) / ".agent-trace" / "traces.jsonl"
    if not traces_path.exists():
        return []
    traces: list[dict[str, Any]] = []
    try:
        for line in traces_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return traces


def _load_local_commit_links(project_dir: str) -> list[dict[str, Any]]:
    """Load all commit links from .agent-trace/commit-links.jsonl."""
    links_path = Path(project_dir) / ".agent-trace" / "commit-links.jsonl"
    if not links_path.exists():
        return []
    links: list[dict[str, Any]] = []
    try:
        for line in links_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    links.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return links


# ===================================================================
# Local attribution engine  (simplified version of service-side logic)
# ===================================================================

def _compute_tier(score: float, signals: list[str]) -> int | None:
    """Map a numeric score + signal list to a confidence tier (1-6) or None.

    Requires at least one *structural* signal (commit_link, content_hash,
    revision_parent, revision_ancestor, range_match, range_overlap).
    Timestamp alone is never sufficient — it would false-positive on every
    manual edit made within the same 24-hour window as any AI trace.
    """
    if score <= 0:
        return None
    # Require at least one structural signal beyond just timestamp
    _STRUCTURAL = {
        "commit_link", "content_hash", "revision_parent",
        "revision_ancestor", "range_match", "range_overlap",
    }
    if not any(s in _STRUCTURAL for s in signals):
        return None
    if score >= 95 and "commit_link" in signals and "content_hash" in signals:
        return 1
    if score >= 80:
        return 2
    if score >= 60:
        return 3
    if score >= 45:
        return 4
    if score >= 25:
        return 5
    return 6


def _tier_to_confidence(tier: int | None) -> float:
    """Convert tier to representative confidence value."""
    if tier is None:
        return 0.0
    return {1: 1.0, 2: 0.999, 3: 0.95, 4: 0.85, 5: 0.70, 6: 0.40}.get(tier, 0.0)


def _hashes_match(hash_a: str, hash_b: str) -> bool:
    """Compare two content hashes, handling different-length prefixes."""
    a = hash_a.removeprefix("sha256:").lower()
    b = hash_b.removeprefix("sha256:").lower()
    min_len = min(len(a), len(b))
    if min_len == 0:
        return False
    return a[:min_len] == b[:min_len]


def _find_matching_file(files: list[dict[str, Any]], file_path: str) -> dict[str, Any] | None:
    """Find the file entry in a trace's files array matching file_path."""
    for f in files:
        if not isinstance(f, dict):
            continue
        trace_path = f.get("path", "")
        if trace_path == file_path:
            return f
        if trace_path.endswith(file_path) or file_path.endswith(trace_path):
            return f
    return None


def _trace_touches_file(trace: dict[str, Any], file_path: str) -> bool:
    """Return True if this trace's files array contains an entry for *file_path*.

    Commit links associate a commit with traces that touched *any* changed file.
    When blaming file F, we must only consider traces that actually touch F.
    """
    files_data = trace.get("files") or []
    if not isinstance(files_data, list):
        return False
    return _find_matching_file(files_data, file_path) is not None


def _collect_ranges(file_entry: dict[str, Any]) -> list[tuple[int, int]]:
    """Collect all (start_line, end_line) ranges from a file entry.

    Same logic as agent-trace-service/attribution._collect_ranges:
    - Top-level file entry start_line/end_line
    - Conversation-level start_line/end_line
    - Inside conversation ranges: conv['ranges'][i] (trace.py format)
    - Inside changes: change start_line/end_line
    """
    ranges: list[tuple[int, int]] = []

    # Top-level range on the file entry
    if "start_line" in file_entry and "end_line" in file_entry:
        try:
            ranges.append((int(file_entry["start_line"]), int(file_entry["end_line"])))
        except (ValueError, TypeError):
            pass

    # Ranges inside conversations (including conv["ranges"][] — trace.py format)
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        if "start_line" in conv and "end_line" in conv:
            try:
                ranges.append((int(conv["start_line"]), int(conv["end_line"])))
            except (ValueError, TypeError):
                pass
        for r in conv.get("ranges", []):
            if isinstance(r, dict) and "start_line" in r and "end_line" in r:
                try:
                    ranges.append((int(r["start_line"]), int(r["end_line"])))
                except (ValueError, TypeError):
                    pass

    # Ranges inside changes
    for change in file_entry.get("changes", []):
        if not isinstance(change, dict):
            continue
        if "start_line" in change and "end_line" in change:
            try:
                ranges.append((int(change["start_line"]), int(change["end_line"])))
            except (ValueError, TypeError):
                pass

    return ranges


def _extract_content_hashes(file_entry: dict[str, Any]) -> list[str]:
    """Extract all content hashes from a file entry.

    Same sources as agent-trace-service/attribution._extract_content_hash:
    - Conversation ranges: conv['ranges'][i]['content_hash'] (trace.py stores here)
    - Conversation-level: conv['content_hash']
    - Change-level: change['content_hash']
    - File-level: file_entry['content_hash']

    We collect all and match the segment hash against any; service picks the
    one that covers the line. Result is equivalent for attribution.
    """
    hashes: list[str] = []

    # Conversation ranges first (trace.py format — hashes live here)
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        for r in conv.get("ranges", []):
            if isinstance(r, dict):
                ch = r.get("content_hash")
                if ch:
                    hashes.append(ch)
        ch = conv.get("content_hash")
        if ch:
            hashes.append(ch)

    # Changes, then file-level
    for change in file_entry.get("changes", []):
        if not isinstance(change, dict):
            continue
        ch = change.get("content_hash")
        if ch:
            hashes.append(ch)

    ch = file_entry.get("content_hash")
    if ch:
        hashes.append(ch)

    return hashes


def _score_trace_local(
    trace: dict[str, Any],
    file_path: str,
    line_number: int,
    content_hash: str | None,
    blame_commit: str,
    blame_parent: str | None,
    has_commit_link: bool,
    linked_trace_ids: list[str],
) -> tuple[float, list[str]]:
    """Score a candidate trace against a blamed line.  Local-data variant."""
    score: float = 0.0
    signals: list[str] = []
    trace_id = trace.get("id", "")

    # --- Commit link match ---
    if has_commit_link and trace_id in linked_trace_ids:
        score += WEIGHT_COMMIT_LINK
        signals.append("commit_link")

    # --- VCS revision match ---
    vcs = trace.get("vcs") or {}
    trace_revision = vcs.get("revision", "")
    if trace_revision and blame_parent:
        if trace_revision == blame_parent:
            score += WEIGHT_REVISION_PARENT
            signals.append("revision_parent")
        elif len(trace_revision) >= 7 and len(blame_parent) >= 7:
            ml = min(len(trace_revision), len(blame_parent))
            if trace_revision[:ml] == blame_parent[:ml]:
                score += WEIGHT_REVISION_PARENT
                signals.append("revision_parent")

    # --- File & line range match ---
    files_data = trace.get("files") or []
    matched_file = _find_matching_file(files_data, file_path)
    if matched_file:
        ranges = _collect_ranges(matched_file)
        range_hit = False
        for start, end in ranges:
            if start <= line_number <= end:
                score += WEIGHT_RANGE_MATCH
                signals.append("range_match")
                range_hit = True
                break
            if (start - 5) <= line_number <= (end + 5):
                score += WEIGHT_RANGE_OVERLAP
                signals.append("range_overlap")
                range_hit = True
                break

        # --- Content hash match ---
        if content_hash:
            file_hashes = _extract_content_hashes(matched_file)
            for fh in file_hashes:
                if _hashes_match(content_hash, fh):
                    score += WEIGHT_CONTENT_HASH
                    signals.append("content_hash")
                    break

    # --- Timestamp match ---
    trace_ts = trace.get("timestamp")
    if trace_ts:
        score += WEIGHT_TIMESTAMP
        signals.append("timestamp_match")

    return score, signals


def _extract_trace_meta(
    trace: dict[str, Any],
    file_path: str,
    line_number: int,
) -> dict[str, Any]:
    """Extract display metadata from a local trace."""
    meta: dict[str, Any] = {
        "trace_id": trace.get("id"),
    }

    # Tool (trace-level)
    tool = trace.get("tool")
    if isinstance(tool, dict):
        meta["tool"] = tool

    files_data = trace.get("files") or []
    matched_file = _find_matching_file(files_data, file_path)

    if matched_file:
        # Search conversations in the matched file entry
        for conv in matched_file.get("conversations", []):
            if not isinstance(conv, dict):
                continue
            contributor = conv.get("contributor") or {}
            if contributor.get("model_id") and not meta.get("model_id"):
                meta["model_id"] = contributor["model_id"]
            if contributor.get("type") and not meta.get("contributor_type"):
                meta["contributor_type"] = contributor["type"]
            if conv.get("url") and not meta.get("conversation_url"):
                meta["conversation_url"] = conv["url"]
            # Only break when we have BOTH model and conversation
            if meta.get("model_id") and meta.get("conversation_url"):
                break

        # Best range
        ranges = _collect_ranges(matched_file)
        best = None
        best_dist = float("inf")
        for start, end in ranges:
            if start <= line_number <= end:
                span = end - start
                if best is None or span < (best[1] - best[0]):
                    best = (start, end)
                    best_dist = 0
            else:
                dist = min(abs(line_number - start), abs(line_number - end))
                if dist < best_dist:
                    best = (start, end)
                    best_dist = dist
        if best:
            meta["matched_range"] = {"start_line": best[0], "end_line": best[1]}

    # Fallback: search ALL file entries for model/conversation if still missing
    if not meta.get("model_id") or not meta.get("conversation_url"):
        for fe in files_data:
            if not isinstance(fe, dict) or fe is matched_file:
                continue
            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not meta.get("model_id"):
                    meta["model_id"] = contributor["model_id"]
                if conv.get("url") and not meta.get("conversation_url"):
                    meta["conversation_url"] = conv["url"]
            if meta.get("model_id") and meta.get("conversation_url"):
                break

    return meta


def _ranges_overlap(
    attr_start: int, attr_end: int,
    seg_start: int, seg_end: int,
) -> bool:
    """Return True if two line ranges overlap."""
    return attr_start <= seg_end and attr_end >= seg_start


def _attribution_type_label(attr_type: str) -> str:
    """Map ledger attribution type to display label."""
    return {"ai": "AI", "human": "Human", "mixed": "Mixed"}.get(attr_type, attr_type)


def _attribute_from_ledger(
    blame_segments: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
    file_path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Try to attribute segments from ledger data.

    Returns (attributed, remaining) — segments resolved by the ledger,
    and segments that need heuristic fallback.

    A single git blame segment may span many ledger entries (e.g. when an
    entire file is created in one commit but edited by multiple AI sessions).
    We therefore emit one attribution entry per overlapping ledger entry,
    clamped to the git blame segment's original line range.

    **Important:** Ledger entries use *original* line numbers (the positions
    as they were when the commit was made).  Git blame segments carry both
    original and final (current) line numbers.  We must use the original
    numbers for ledger lookups and then map back to final numbers for
    display via the offset ``final_start - orig_start``.
    """
    attributed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []

    for seg in blame_segments:
        commit_sha = seg["commit_sha"]
        ledger = ledgers.get(commit_sha)

        if ledger and file_path in ledger.get("files", {}):
            file_ledger = ledger["files"][file_path]
            line_attrs = file_ledger.get("line_attributions", [])

            # Original line range for this segment (as recorded in the commit)
            orig_start = seg.get("orig_start_line", seg["start_line"])
            orig_end = seg.get("orig_end_line", seg["end_line"])
            # Offset to convert original -> final line numbers
            offset = seg["start_line"] - orig_start

            # Collect ALL overlapping ledger entries using ORIGINAL line numbers.
            overlapping: list[tuple[int, int, dict[str, Any]]] = []
            for la in sorted(line_attrs, key=lambda x: x.get("start_line", 0)):
                la_start = la.get("start_line", 0)
                la_end = la.get("end_line", 0)
                if _ranges_overlap(la_start, la_end, orig_start, orig_end):
                    clamped_start = max(la_start, orig_start)
                    clamped_end = min(la_end, orig_end)
                    overlapping.append((clamped_start, clamped_end, la))

            if overlapping:
                # Emit one attribution per ledger entry, mapped to final line numbers
                for clamped_orig_start, clamped_orig_end, la in overlapping:
                    final_start = clamped_orig_start + offset
                    final_end = clamped_orig_end + offset
                    attr_type = la.get("type", "unknown")
                    is_ai = attr_type == "ai"
                    is_mixed = attr_type == "mixed"
                    attributed.append({
                        "start_line": final_start,
                        "end_line": final_end,
                        "tier": 1 if is_ai else (3 if is_mixed else None),
                        "confidence": 1.0 if is_ai else (0.95 if is_mixed else 0.0),
                        "trace_id": la.get("trace_id"),
                        "model_id": la.get("model_id"),
                        "contributor_type": attr_type,
                        "tool": None,
                        "conversation_url": la.get("conversation_url"),
                        "conversation_summary": None,
                        "matched_range": {
                            "start_line": la.get("start_line"),
                            "end_line": la.get("end_line"),
                        },
                        "commit_sha": commit_sha,
                        "signals": ["ledger"],
                        "commit_link_match": True,
                        "content_hash_match": is_ai,
                        "source": "ledger",
                        "attribution_label": _attribution_type_label(attr_type),
                    })

                # If there are gaps in ledger coverage within this segment,
                # pass those uncovered line ranges to the heuristic path.
                covered_orig_start = overlapping[0][0]
                covered_orig_end = overlapping[-1][1]
                if orig_start < covered_orig_start:
                    gap_seg = dict(seg)
                    gap_seg["start_line"] = seg["start_line"]
                    gap_seg["end_line"] = covered_orig_start + offset - 1
                    gap_seg["orig_start_line"] = orig_start
                    gap_seg["orig_end_line"] = covered_orig_start - 1
                    n_lines = covered_orig_start - orig_start
                    gap_seg["content_lines"] = seg["content_lines"][:n_lines]
                    remaining.append(gap_seg)
                if covered_orig_end < orig_end:
                    gap_seg = dict(seg)
                    n_before = covered_orig_end - orig_start + 1
                    gap_seg["start_line"] = covered_orig_end + offset + 1
                    gap_seg["end_line"] = seg["end_line"]
                    gap_seg["orig_start_line"] = covered_orig_end + 1
                    gap_seg["orig_end_line"] = orig_end
                    gap_seg["content_lines"] = seg["content_lines"][n_before:]
                    remaining.append(gap_seg)
                continue

        remaining.append(seg)

    return attributed, remaining


def _attribute_locally(
    blame_segments: list[dict[str, Any]],
    traces: list[dict[str, Any]],
    commit_links: list[dict[str, Any]],
    file_path: str,
    cwd: str | None = None,
    ledgers: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Run multi-tier attribution against local trace data.

    Returns a list of attribution dicts (one per segment), ready for
    display or JSON serialization.
    """
    # --- Ledger-first path: deterministic attribution ---
    ledger_results: list[dict[str, Any]] = []
    heuristic_segments = blame_segments
    if ledgers:
        ledger_results, heuristic_segments = _attribute_from_ledger(
            blame_segments, ledgers, file_path,
        )

    # Build commit_sha -> commit_link index
    link_by_commit: dict[str, dict[str, Any]] = {}
    for cl in commit_links:
        sha = cl.get("commit_sha", "")
        if sha:
            link_by_commit[sha] = cl

    # Cache parent SHAs to avoid repeated git calls
    parent_cache: dict[str, str | None] = {}
    date_cache: dict[str, str | None] = {}

    results: list[dict[str, Any]] = []

    for seg in heuristic_segments:
        commit_sha = seg["commit_sha"]
        start_line = seg["start_line"]
        end_line = seg["end_line"]
        content_hash = _content_hash_for_segment(seg["content_lines"])
        representative_line = (start_line + end_line) // 2

        # Get parent SHA (cached)
        if commit_sha not in parent_cache:
            parent_cache[commit_sha] = _get_parent_sha(commit_sha, cwd=cwd)
        parent_sha = parent_cache[commit_sha]

        # Get commit date (cached)
        if commit_sha not in date_cache:
            date_cache[commit_sha] = _get_commit_date(commit_sha, cwd=cwd)
        commit_date = date_cache[commit_sha]

        # Check for commit link
        commit_link = link_by_commit.get(commit_sha)
        linked_trace_ids: list[str] = (
            commit_link.get("trace_ids", []) if commit_link else []
        )
        has_commit_link = commit_link is not None

        # Find candidate traces
        candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        # Path A: From commit link
        if linked_trace_ids:
            linked_set = set(linked_trace_ids)
            for t in traces:
                tid = t.get("id", "")
                if tid in linked_set and tid not in seen_ids:
                    candidates.append(t)
                    seen_ids.add(tid)

        # Path B: Parent revision match + file path match
        if parent_sha:
            for t in traces:
                tid = t.get("id", "")
                if tid in seen_ids:
                    continue
                vcs = t.get("vcs") or {}
                if vcs.get("revision") == parent_sha:
                    if _find_matching_file(t.get("files", []), file_path):
                        candidates.append(t)
                        seen_ids.add(tid)

        # Path C: Timestamp window fallback (if few candidates)
        if len(candidates) < 5 and commit_date:
            try:
                commit_dt = datetime.fromisoformat(commit_date)
                window_start = commit_dt - timedelta(hours=24)
                window_end = commit_dt + timedelta(hours=1)
                for t in traces:
                    tid = t.get("id", "")
                    if tid in seen_ids:
                        continue
                    ts_str = t.get("timestamp")
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if window_start <= ts <= window_end:
                            if _find_matching_file(t.get("files", []), file_path):
                                candidates.append(t)
                                seen_ids.add(tid)
                    except (ValueError, TypeError):
                        continue
            except (ValueError, TypeError):
                pass

        # Only consider traces that actually touch the blamed file (same as remote)
        candidates = [t for t in candidates if _trace_touches_file(t, file_path)]

        # Score candidates
        best_score: float = 0.0
        best_trace: dict[str, Any] | None = None
        best_signals: list[str] = []

        for t in candidates:
            score, sigs = _score_trace_local(
                t, file_path, representative_line, content_hash,
                commit_sha, parent_sha,
                has_commit_link, linked_trace_ids,
            )
            if score > best_score:
                best_score = score
                best_trace = t
                best_signals = sigs

        # Build attribution result
        tier = None
        if best_trace is not None and best_score > 0:
            tier = _compute_tier(best_score, best_signals)

        # Require some evidence (same as remote): range, or commit_link+content_hash, or commit_link+revision_parent
        if best_trace is not None and tier is not None:
            has_range_evidence = "range_match" in best_signals or "range_overlap" in best_signals
            has_strong_evidence = "commit_link" in best_signals and "content_hash" in best_signals
            has_commit_and_revision = "commit_link" in best_signals and "revision_parent" in best_signals
            if not (has_range_evidence or has_strong_evidence or has_commit_and_revision):
                tier = None

        if best_trace is not None and tier is not None:
            confidence = _tier_to_confidence(tier)
            meta = _extract_trace_meta(best_trace, file_path, representative_line)

            # Enrich from other linked traces if best trace is missing info
            if (not meta.get("model_id") or not meta.get("conversation_url")) and linked_trace_ids:
                for t in candidates:
                    if t.get("id") == best_trace.get("id"):
                        continue
                    other_meta = _extract_trace_meta(t, file_path, representative_line)
                    if not meta.get("model_id") and other_meta.get("model_id"):
                        meta["model_id"] = other_meta["model_id"]
                    if not meta.get("conversation_url") and other_meta.get("conversation_url"):
                        meta["conversation_url"] = other_meta["conversation_url"]
                    if meta.get("model_id") and meta.get("conversation_url"):
                        break

            # Load conversation summary from file URL
            conv_summary = _load_conversation_summary(meta.get("conversation_url"))

            results.append({
                "start_line": start_line,
                "end_line": end_line,
                "tier": tier,
                "confidence": confidence,
                "trace_id": meta.get("trace_id"),
                "model_id": meta.get("model_id"),
                "contributor_type": meta.get("contributor_type", "unknown"),
                "tool": meta.get("tool"),
                "conversation_url": meta.get("conversation_url"),
                "conversation_summary": conv_summary,
                "matched_range": meta.get("matched_range"),
                "commit_sha": commit_sha,
                "signals": best_signals,
                "commit_link_match": "commit_link" in best_signals,
                "content_hash_match": "content_hash" in best_signals,
            })
        else:
            # No attribution (no matching trace, or only weak signals like timestamp)
            results.append({
                "start_line": start_line,
                "end_line": end_line,
                "tier": None,
                "confidence": 0.0,
                "trace_id": None,
                "model_id": None,
                "contributor_type": None,
                "tool": None,
                "conversation_url": None,
                "conversation_summary": None,
                "matched_range": None,
                "commit_sha": commit_sha,
                "signals": [],
                "commit_link_match": False,
                "content_hash_match": False,
            })

    # Combine ledger results with heuristic results, sorted by start_line
    all_results = ledger_results + results
    all_results.sort(key=lambda a: (a.get("start_line", 0), a.get("end_line", 0)))

    return all_results


# ===================================================================
# Merge adjacent attributions
# ===================================================================

def _merge_attributions(attributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent segments that share the same attribution (trace + tier)."""
    if not attributions:
        return []
    merged: list[dict[str, Any]] = []
    for entry in attributions:
        if merged:
            prev = merged[-1]
            if (
                prev["end_line"] + 1 >= entry["start_line"]
                and prev["trace_id"] == entry["trace_id"]
                and prev["tier"] == entry["tier"]
            ):
                prev["end_line"] = entry["end_line"]
                continue
        merged.append(dict(entry))  # shallow copy
    return merged


# ===================================================================
# Remote mode
# ===================================================================

def _blame_remote(
    config: dict[str, Any],
    file_path: str,
    blame_segments: list[dict[str, Any]],
    cwd: str | None = None,
    ledgers: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """POST blame data to the remote agent-trace-service and return attributions.

    If local ledgers are available, segments covered by them are attributed
    deterministically without a round-trip to the service.
    """
    # Try ledger-first attribution for segments that have local ledgers
    ledger_results: list[dict[str, Any]] = []
    remote_segments = blame_segments
    if ledgers:
        ledger_results, remote_segments = _attribute_from_ledger(
            blame_segments, ledgers, file_path,
        )

    if not remote_segments:
        # All segments resolved by ledger
        return ledger_results

    project_id = config.get("project_id")
    auth_token = get_auth_token(config)
    service_url = get_service_url(config)

    if not project_id or not auth_token:
        print("agent-trace blame: remote mode requires project_id and auth token.",
              file=sys.stderr)
        return ledger_results

    # Cache parent SHAs and dates
    parent_cache: dict[str, str | None] = {}
    date_cache: dict[str, str | None] = {}

    # Build the POST payload
    blame_data: list[dict[str, Any]] = []
    for seg in remote_segments:
        commit_sha = seg["commit_sha"]

        if commit_sha not in parent_cache:
            parent_cache[commit_sha] = _get_parent_sha(commit_sha, cwd=cwd)
        if commit_sha not in date_cache:
            date_cache[commit_sha] = _get_commit_date(commit_sha, cwd=cwd)

        blame_data.append({
            "start_line": seg["start_line"],
            "end_line": seg["end_line"],
            "commit_sha": commit_sha,
            "parent_sha": parent_cache[commit_sha],
            "content_hash": _content_hash_for_segment(seg["content_lines"]),
            "timestamp": date_cache[commit_sha],
        })

    body = json.dumps({
        "project_id": project_id,
        "file_path": file_path,
        "blame_data": blame_data,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{service_url}/api/v1/blame",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {auth_token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"agent-trace blame: service responded {e.code}: {e.read().decode()}",
              file=sys.stderr)
        return []
    except Exception as e:
        print(f"agent-trace blame: service unreachable: {e}", file=sys.stderr)
        return []

    remote_results = data.get("attributions", [])

    # Merge ledger and remote results, sorted by start_line
    all_results = ledger_results + remote_results
    all_results.sort(key=lambda a: (a.get("start_line", 0), a.get("end_line", 0)))
    return all_results


# ===================================================================
# Output formatting
# ===================================================================

# ANSI colour codes
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_RESET = "\033[0m"

_TIER_DISPLAY = {
    1: f"{_GREEN}[Tier 1 \u2713\u2713\u2713]{_RESET}",
    2: f"{_GREEN}[Tier 2 \u2713\u2713]{_RESET}",
    3: f"{_CYAN}[Tier 3 \u2713\u2713]{_RESET}",
    4: f"{_YELLOW}[Tier 4 \u2713]{_RESET}",
    5: f"{_MAGENTA}[Tier 5 ~]{_RESET}",
    6: f"{_DIM}[Tier 6 ?]{_RESET}",
}


def _format_line_range(start: int, end: int) -> str:
    if start == end:
        return f"L{start}"
    return f"L{start}-{end}"


def _format_terminal(file_path: str, attributions: list[dict[str, Any]]) -> str:
    """Format attributions for terminal display."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {_BOLD}{file_path}{_RESET}")
    lines.append("")

    for attr in attributions:
        start = attr.get("start_line", 0)
        end = attr.get("end_line", 0)
        tier = attr.get("tier")
        lr = _format_line_range(start, end)
        source = attr.get("source", "")

        if tier is None:
            # Check if this is a ledger "human" attribution
            if source == "ledger":
                label = attr.get("attribution_label", "Human")
                lines.append(f"  {lr:<12}{_DIM}[{label}]{_RESET}")
            else:
                lines.append(f"  {lr:<12}{_DIM}[no ai attribution]{_RESET}")
            continue

        # Ledger-sourced attribution gets a deterministic label
        if source == "ledger":
            label = attr.get("attribution_label", "AI")
            if label == "AI":
                tier_label = f"{_GREEN}[{label}]{_RESET}"
            elif label == "Mixed":
                tier_label = f"{_YELLOW}[{label}]{_RESET}"
            else:
                tier_label = f"{_DIM}[{label}]{_RESET}"
        else:
            tier_label = _TIER_DISPLAY.get(tier, f"[Tier {tier}]")

        # Model + tool (remote returns model_id in contributor; support both)
        model_id = attr.get("model_id") or (attr.get("contributor") or {}).get("model_id") or ""
        tool = attr.get("tool")
        tool_name = ""
        if isinstance(tool, dict):
            tool_name = tool.get("name", "")
        elif isinstance(tool, str):
            tool_name = tool

        model_tool = model_id
        if tool_name:
            model_tool = f"{model_id} via {tool_name}" if model_id else tool_name

        lines.append(f"  {lr:<12}{tier_label} {model_tool}")

        # Model info on its own line if present
        if model_id:
            lines.append(f"              {_DIM}model: {model_id}{_RESET}")

        # Conversation summary (if available)
        conv_summary = attr.get("conversation_summary") or ""
        conv_url = attr.get("conversation_url") or ""
        if conv_summary:
            # Show first line of summary inline, truncated
            summary_line = conv_summary.replace("\n", " ").strip()
            if len(summary_line) > 120:
                summary_line = summary_line[:120] + "..."
            lines.append(f"              conversation: \"{summary_line}\"")
        elif conv_url:
            lines.append(f"              conversation: {conv_url}")

        # Trace / commit (full IDs)
        trace_id = attr.get("trace_id") or ""
        commit_sha = attr.get("commit_sha") or ""
        detail_parts = []
        if trace_id:
            detail_parts.append(f"trace: {trace_id}")
        if commit_sha:
            detail_parts.append(f"commit: {commit_sha[:12]}")
        if detail_parts:
            lines.append(f"              {_DIM}{' | '.join(detail_parts)}{_RESET}")

    lines.append("")
    return "\n".join(lines)


def _format_json(file_path: str, attributions: list[dict[str, Any]]) -> str:
    """Format attributions as JSON."""
    # Strip internal fields, keep clean output
    clean: list[dict[str, Any]] = []
    for attr in attributions:
        entry: dict[str, Any] = {
            "start_line": attr.get("start_line"),
            "end_line": attr.get("end_line"),
            "tier": attr.get("tier"),
            "confidence": attr.get("confidence", 0.0),
        }
        if attr.get("trace_id"):
            entry["trace_id"] = attr["trace_id"]
        model_id = attr.get("model_id") or (attr.get("contributor") or {}).get("model_id")
        if model_id:
            entry["model_id"] = model_id
        contributor_type = attr.get("contributor_type") or (attr.get("contributor") or {}).get("type")
        if contributor_type:
            entry["contributor_type"] = contributor_type
        tool = attr.get("tool")
        if isinstance(tool, dict):
            entry["tool"] = tool.get("name", "")
        elif isinstance(tool, str):
            entry["tool"] = tool
        if attr.get("commit_sha"):
            entry["commit_sha"] = attr["commit_sha"]
        if attr.get("conversation_url"):
            entry["conversation_url"] = attr["conversation_url"]
        if attr.get("conversation_summary"):
            entry["conversation_summary"] = attr["conversation_summary"]
        if attr.get("signals"):
            entry["signals"] = attr["signals"]
        if attr.get("commit_link_match"):
            entry["commit_link_match"] = True
        if attr.get("content_hash_match"):
            entry["content_hash_match"] = True
        if attr.get("source"):
            entry["source"] = attr["source"]
        if attr.get("attribution_label"):
            entry["attribution_label"] = attr["attribution_label"]
        clean.append(entry)

    output = {"file": file_path, "attributions": clean}
    return json.dumps(output, indent=2)


# ===================================================================
# Main entry point
# ===================================================================

def blame_file(
    file_path: str,
    *,
    line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    min_tier: int = 6,
    json_output: bool = False,
    project_dir: str | None = None,
) -> str | None:
    """Run AI blame on a file and print results (or return JSON when json_output=True).

    Parameters
    ----------
    file_path : str
        Path to the file to blame.
    line : int | None
        Specific line number to blame.
    start_line, end_line : int | None
        Line range to blame (from --range).
    min_tier : int
        Minimum confidence tier to display (1-6).
    json_output : bool
        If True, return JSON string instead of printing; caller can print or use it.
    project_dir : str | None
        If set, use this as the project (git) root and config root instead of os.getcwd().
        Used by the file viewer when running blame for an explicit project path.

    Returns
    -------
    str | None
        When json_output is True, returns the JSON string; otherwise None (output was printed).
    """
    # Resolve the file path relative to git root
    cwd = project_dir if project_dir else os.getcwd()
    abs_path = os.path.abspath(os.path.join(cwd, file_path))

    if not os.path.isfile(abs_path):
        if json_output:
            return None
        print(f"agent-trace blame: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Determine the git-relative path
    git_root = _git("rev-parse", "--show-toplevel", cwd=cwd)
    if git_root is None:
        if json_output:
            return None
        print("agent-trace blame: not a git repository", file=sys.stderr)
        sys.exit(1)

    try:
        rel_path = os.path.relpath(abs_path, git_root)
    except ValueError:
        rel_path = file_path

    # Handle --line -> single-line range
    if line is not None:
        start_line = line
        end_line = line

    # Run git blame --porcelain
    raw = _git_blame_porcelain(
        rel_path,
        start_line=start_line,
        end_line=end_line,
        cwd=git_root,
    )
    if raw is None:
        if json_output:
            return None
        print(f"agent-trace blame: git blame failed for {file_path}", file=sys.stderr)
        sys.exit(1)

    # Parse and group
    records = _parse_blame_porcelain(raw)
    if not records:
        if json_output:
            return None
        print(f"agent-trace blame: no blame data for {file_path}", file=sys.stderr)
        sys.exit(1)

    segments = _group_into_segments(records)

    # Determine storage mode
    config = get_project_config(project_dir=cwd)
    if config is None:
        config = {"storage": "local"}
    storage = config.get("storage", "local")

    # Load ledgers for deterministic attribution
    ledgers = load_local_ledgers(git_root)

    # Run attribution
    if storage == "remote":
        attributions = _blame_remote(config, rel_path, segments, cwd=git_root, ledgers=ledgers)
    else:
        traces = _load_local_traces(git_root)
        commit_links = _load_local_commit_links(git_root)
        raw_attrs = _attribute_locally(
            segments, traces, commit_links, rel_path, cwd=git_root,
            ledgers=ledgers,
        )
        attributions = _merge_attributions(raw_attrs)

    # Filter by min_tier
    if min_tier < 6:
        attributions = [
            a for a in attributions
            if a.get("tier") is None or (a.get("tier") is not None and a["tier"] <= min_tier)
        ]

    # Output
    if json_output:
        result = _format_json(rel_path, attributions)
        return result
    print(_format_terminal(rel_path, attributions))
    return None
