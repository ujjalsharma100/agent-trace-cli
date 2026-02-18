"""
Attribution ledger — deterministic per-line code attribution at commit time.

The ledger is built by the post-commit hook immediately after ``git commit``.
It compares committed file contents against trace-level line hashes to produce
a definitive mapping from each changed line to its origin (AI, human, or mixed).

This replaces heuristic scoring for commits that have a ledger — the blame
algorithm checks the ledger first and only falls back to heuristics when no
ledger exists.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# -------------------------------------------------------------------
# Git helpers
# -------------------------------------------------------------------

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


def _git_raw(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command and return raw stdout (not stripped), or None."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


# -------------------------------------------------------------------
# Diff parsing
# -------------------------------------------------------------------

def _parse_diff_ranges(diff_output: str) -> list[tuple[int, int]]:
    """Parse unified diff ``@@`` headers to find added/modified line ranges.

    Returns a list of ``(start_line, end_line)`` tuples (1-indexed) for
    lines that are new or changed in the *new* side of the diff.
    """
    ranges: list[tuple[int, int]] = []
    # Match @@ -old_start[,old_count] +new_start[,new_count] @@
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    in_hunk = False
    hunk_new_start = 0
    current_new_line = 0
    add_start: int | None = None

    for line in diff_output.split("\n"):
        m = hunk_re.match(line)
        if m:
            # Flush any open add range from previous hunk
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
            hunk_new_start = int(m.group(1))
            current_new_line = hunk_new_start
            in_hunk = True
            continue

        if not in_hunk:
            continue

        if line.startswith("\\"):
            # "\ No newline at end of file"
            continue

        if line.startswith("+"):
            # Added line
            if add_start is None:
                add_start = current_new_line
            current_new_line += 1
        elif line.startswith("-"):
            # Deleted line — flush any add range
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
            # Deleted lines don't advance new-side counter
        else:
            # Context line
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
            current_new_line += 1

    # Flush final add range
    if add_start is not None:
        ranges.append((add_start, current_new_line - 1))

    return ranges


# -------------------------------------------------------------------
# Line hashing
# -------------------------------------------------------------------

def _compute_file_line_hashes(content: str) -> dict[int, str]:
    """Compute per-line hashes for every line in a file.

    Returns ``{line_number: "sha256:..."}``, 1-indexed.
    """
    lines = content.split("\n")
    # Strip trailing empty line from trailing newline
    if lines and lines[-1] == "":
        lines = lines[:-1]
    result: dict[int, str] = {}
    for i, line in enumerate(lines):
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
        result[i + 1] = f"sha256:{h}"
    return result


# -------------------------------------------------------------------
# Trace indexing
# -------------------------------------------------------------------

def _build_trace_hash_index(
    traces: list[dict[str, Any]],
    file_path: str,
) -> dict[str, dict[str, Any]]:
    """Build a map from line content hash → trace metadata.

    For each candidate trace that touches ``file_path``, extract all
    ``line_hashes`` entries and map ``hash_value → {trace_id, model_id,
    tool, conversation_url, edit_sequence}``.

    When multiple traces claim the same hash, the one with the highest
    ``edit_sequence`` wins (latest edit takes precedence).
    """
    index: dict[str, dict[str, Any]] = {}

    for trace in traces:
        trace_id = trace.get("id", "")
        meta = trace.get("metadata") or {}
        edit_seq = meta.get("edit_sequence")

        # Extract model_id and conversation_url from first conversation
        model_id = None
        conversation_url = None
        tool = trace.get("tool")

        for fe in trace.get("files", []):
            if not isinstance(fe, dict):
                continue
            fpath = fe.get("path", "")
            if fpath != file_path and not fpath.endswith(file_path) and not file_path.endswith(fpath):
                continue

            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not model_id:
                    model_id = contributor["model_id"]
                if conv.get("url") and not conversation_url:
                    conversation_url = conv["url"]

                for r in conv.get("ranges", []):
                    if not isinstance(r, dict):
                        continue
                    for lh in r.get("line_hashes", []):
                        if not isinstance(lh, dict):
                            continue
                        h = lh.get("hash", "")
                        if not h:
                            continue

                        existing = index.get(h)
                        if existing is not None:
                            # Tiebreak by edit_sequence (highest wins)
                            existing_seq = existing.get("edit_sequence")
                            if edit_seq is not None and (existing_seq is None or edit_seq > existing_seq):
                                pass  # Will overwrite below
                            else:
                                continue

                        index[h] = {
                            "trace_id": trace_id,
                            "model_id": model_id,
                            "tool": tool,
                            "conversation_url": conversation_url,
                            "edit_sequence": edit_seq,
                        }

    return index


def _build_cross_file_hash_index(
    traces: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build a map from line content hash → trace metadata across ALL files.

    Unlike ``_build_trace_hash_index``, this does NOT filter by file path.
    Used as a fallback when no traces directly claim a file — catches cases
    where AI-generated code was moved to a different file before committing.
    """
    index: dict[str, dict[str, Any]] = {}

    for trace in traces:
        trace_id = trace.get("id", "")
        meta = trace.get("metadata") or {}
        edit_seq = meta.get("edit_sequence")

        model_id = None
        conversation_url = None
        tool = trace.get("tool")

        for fe in trace.get("files", []):
            if not isinstance(fe, dict):
                continue

            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not model_id:
                    model_id = contributor["model_id"]
                if conv.get("url") and not conversation_url:
                    conversation_url = conv["url"]

                for r in conv.get("ranges", []):
                    if not isinstance(r, dict):
                        continue
                    for lh in r.get("line_hashes", []):
                        if not isinstance(lh, dict):
                            continue
                        h = lh.get("hash", "")
                        if not h:
                            continue

                        existing = index.get(h)
                        if existing is not None:
                            existing_seq = existing.get("edit_sequence")
                            if edit_seq is not None and (existing_seq is None or edit_seq > existing_seq):
                                pass  # Will overwrite below
                            else:
                                continue

                        index[h] = {
                            "trace_id": trace_id,
                            "model_id": model_id,
                            "tool": tool,
                            "conversation_url": conversation_url,
                            "edit_sequence": edit_seq,
                        }

    return index


def _build_range_claim_index(
    traces: list[dict[str, Any]],
    file_path: str,
) -> dict[int, list[dict[str, Any]]]:
    """Build a map from line_number → list of trace claims.

    For each trace that touches ``file_path``, check all ranges and record
    which line numbers they claim.
    """
    index: dict[int, list[dict[str, Any]]] = {}

    for trace in traces:
        trace_id = trace.get("id", "")
        meta = trace.get("metadata") or {}

        model_id = None
        conversation_url = None
        tool = trace.get("tool")

        for fe in trace.get("files", []):
            if not isinstance(fe, dict):
                continue
            fpath = fe.get("path", "")
            if fpath != file_path and not fpath.endswith(file_path) and not file_path.endswith(fpath):
                continue

            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not model_id:
                    model_id = contributor["model_id"]
                if conv.get("url") and not conversation_url:
                    conversation_url = conv["url"]

                for r in conv.get("ranges", []):
                    if not isinstance(r, dict):
                        continue
                    start = r.get("start_line")
                    end = r.get("end_line")
                    if start is None or end is None:
                        continue
                    try:
                        start = int(start)
                        end = int(end)
                    except (ValueError, TypeError):
                        continue

                    claim = {
                        "trace_id": trace_id,
                        "model_id": model_id,
                        "tool": tool,
                        "conversation_url": conversation_url,
                        "edit_sequence": meta.get("edit_sequence"),
                    }
                    for ln in range(start, end + 1):
                        index.setdefault(ln, []).append(claim)

    return index


# -------------------------------------------------------------------
# Candidate trace finder
# -------------------------------------------------------------------

def _find_candidate_traces(
    project_dir: str,
    parent_sha: str | None,
    committed_at: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Find traces that could have contributed to the current commit.

    Returns ``(revision_matched, timestamp_matched)`` — two separate lists:

    - **revision_matched**: Traces whose ``vcs.revision`` equals the parent
      SHA.  These were recorded against the exact version of the files that
      existed before this commit, so both their hash data AND range claims
      are valid.
    - **timestamp_matched**: Traces found via time-window fallback.  Their
      ranges refer to a potentially different file version, so only their
      content hashes should be used (not range claims).
    """
    traces_path = Path(project_dir) / ".agent-trace" / "traces.jsonl"
    if not traces_path.exists():
        return [], []

    all_traces: list[dict[str, Any]] = []
    try:
        for line in traces_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    all_traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return [], []

    revision_matched: list[dict[str, Any]] = []
    timestamp_matched: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Strategy 1: Match by parent revision (strongest — range claims valid)
    if parent_sha:
        for t in all_traces:
            vcs = t.get("vcs") or {}
            if vcs.get("revision") == parent_sha:
                tid = t.get("id", "")
                if tid and tid not in seen_ids:
                    revision_matched.append(t)
                    seen_ids.add(tid)

    # Strategy 2: Time window fallback (hash matching only — no range claims)
    if committed_at:
        try:
            commit_dt = datetime.fromisoformat(committed_at)
            window_start = commit_dt - timedelta(hours=24)
            window_end = commit_dt + timedelta(hours=1)
            for t in all_traces:
                tid = t.get("id", "")
                if tid in seen_ids:
                    continue
                ts_str = t.get("timestamp")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if window_start <= ts <= window_end:
                        timestamp_matched.append(t)
                        seen_ids.add(tid)
                except (ValueError, TypeError):
                    continue
        except (ValueError, TypeError):
            pass

    return revision_matched, timestamp_matched


# -------------------------------------------------------------------
# Ledger construction
# -------------------------------------------------------------------

def build_attribution_ledger(project_dir: str | None = None) -> dict[str, Any] | None:
    """Build a per-line attribution ledger for the current HEAD commit.

    Algorithm:
      1. Get HEAD and parent SHAs, changed files, commit date
      2. Find candidate traces from local storage
      3. For each changed file:
         a. Read committed content from git
         b. Parse diff to find changed line ranges
         c. Compute per-line hashes for committed content
         d. Build hash index and range claim index from traces
         e. Attribute each changed line: hash match → ai, range claim → mixed, else → human
         f. Merge contiguous lines with same attribution into segments
      4. Build and return ledger dict

    Returns None if no data available (no parent, no changed files, etc.).
    """
    if project_dir is None:
        import os
        project_dir = os.getcwd()

    commit_sha = _git("rev-parse", "HEAD", cwd=project_dir)
    if not commit_sha:
        return None

    parent_sha = _git("rev-parse", "HEAD^", cwd=project_dir)
    committed_at = _git("log", "-1", "--format=%aI", "HEAD", cwd=project_dir)

    # Get changed files
    if parent_sha:
        changed_out = _git("diff", "--name-only", "HEAD^", "HEAD", cwd=project_dir)
    else:
        # Initial commit
        changed_out = _git("diff", "--name-only", "--diff-filter=ACMR",
                           "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18", "HEAD",
                           cwd=project_dir)

    if not changed_out:
        return None

    changed_files = [f for f in changed_out.splitlines() if f.strip()]
    if not changed_files:
        return None

    # Find candidate traces (may be empty for pure-human commits).
    # revision_matched: traces at parent revision → range claims valid
    # timestamp_matched: traces in time window → hash matching only
    revision_matched, timestamp_matched = _find_candidate_traces(
        project_dir, parent_sha, committed_at,
    )
    all_candidates = revision_matched + timestamp_matched

    # Hash of empty/whitespace-only lines — too common to be meaningful
    _TRIVIAL_HASHES: set[str] = set()
    for trivial in ("", " ", "\t", "  ", "    ", "\t\t"):
        _TRIVIAL_HASHES.add(f"sha256:{hashlib.sha256(trivial.encode('utf-8')).hexdigest()[:16]}")

    # Collect all trace IDs used
    used_trace_ids: set[str] = set()
    files_attributions: dict[str, dict[str, Any]] = {}

    for file_path in changed_files:
        # Read committed file content
        file_content = _git_raw("show", f"HEAD:{file_path}", cwd=project_dir)
        if file_content is None:
            continue

        # Get diff for this file
        if parent_sha:
            diff_output = _git_raw("diff", "HEAD^", "HEAD", "--", file_path, cwd=project_dir)
        else:
            diff_output = _git_raw("diff", "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18",
                                   "HEAD", "--", file_path, cwd=project_dir)

        diff_ranges: list[tuple[int, int]] = []
        if diff_output:
            diff_ranges = _parse_diff_ranges(diff_output)

        if not diff_ranges:
            continue

        # Build the set of changed line numbers
        changed_lines: set[int] = set()
        for start, end in diff_ranges:
            for ln in range(start, end + 1):
                changed_lines.add(ln)

        # Compute line hashes for the committed file
        file_line_hashes = _compute_file_line_hashes(file_content)

        # Hash index: use ALL candidates (revision + timestamp).
        # Content-hash matching is position-independent so it's safe
        # across file versions.
        trace_hash_index = _build_trace_hash_index(all_candidates, file_path)

        # Range claim index: ONLY from revision-matched traces.
        # Range claims are position-based and only valid when the trace
        # describes the same version of the file (parent revision).
        range_claim_index = _build_range_claim_index(revision_matched, file_path)

        # Cross-file hash fallback: if no traces directly claim this file,
        # search all traces' line hashes regardless of file path.
        # Only use revision-matched traces for cross-file to avoid
        # picking up hashes from unrelated sessions.
        if not trace_hash_index and not range_claim_index:
            trace_hash_index = _build_cross_file_hash_index(revision_matched)
            # If still empty, try timestamp-matched as a last resort
            if not trace_hash_index:
                trace_hash_index = _build_cross_file_hash_index(timestamp_matched)

        # Attribute each changed line
        line_attrs: list[dict[str, Any]] = []
        for ln in sorted(changed_lines):
            line_hash = file_line_hashes.get(ln)
            if not line_hash:
                continue

            # Skip trivial (empty / whitespace-only) lines — their hashes
            # match across all traces and carry no authorship signal.
            if line_hash in _TRIVIAL_HASHES:
                line_attrs.append({
                    "line": ln,
                    "type": "human",
                    "trace_id": None,
                    "model_id": None,
                    "conversation_url": None,
                })
                continue

            # Check hash index first (strongest signal — exact content match)
            trace_meta = trace_hash_index.get(line_hash)
            if trace_meta:
                attr_type = "ai"
                meta = trace_meta
                used_trace_ids.add(meta["trace_id"])
            elif ln in range_claim_index:
                # Line is in a trace's range but content hash didn't match
                # → human edited an AI-originated region
                attr_type = "mixed"
                claims = range_claim_index[ln]
                # Pick the claim with highest edit_sequence
                best = max(claims, key=lambda c: c.get("edit_sequence") or -1)
                meta = best
                used_trace_ids.add(meta["trace_id"])
            else:
                attr_type = "human"
                meta = {}

            line_attrs.append({
                "line": ln,
                "type": attr_type,
                "trace_id": meta.get("trace_id"),
                "model_id": meta.get("model_id"),
                "conversation_url": meta.get("conversation_url"),
            })

        if not line_attrs:
            continue

        # Merge contiguous lines with same attribution into segments
        segments = _merge_line_attrs(line_attrs)
        files_attributions[file_path] = {"line_attributions": segments}

    if not files_attributions:
        return None

    ledger: dict[str, Any] = {
        "version": "1.0",
        "commit_sha": commit_sha,
        "parent_sha": parent_sha,
        "committed_at": committed_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trace_ids": sorted(used_trace_ids),
        "files": files_attributions,
    }

    return ledger


def _merge_line_attrs(line_attrs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge contiguous lines with the same attribution type and trace into segments."""
    if not line_attrs:
        return []

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for la in line_attrs:
        if (
            current is not None
            and current["end_line"] + 1 == la["line"]
            and current["type"] == la["type"]
            and current.get("trace_id") == la.get("trace_id")
        ):
            current["end_line"] = la["line"]
        else:
            if current is not None:
                segments.append(current)
            current = {
                "start_line": la["line"],
                "end_line": la["line"],
                "type": la["type"],
                "trace_id": la.get("trace_id"),
                "model_id": la.get("model_id"),
                "conversation_url": la.get("conversation_url"),
            }

    if current is not None:
        segments.append(current)

    return segments


# -------------------------------------------------------------------
# Local ledger storage
# -------------------------------------------------------------------

def store_ledger_local(ledger: dict[str, Any], project_dir: str) -> None:
    """Append a ledger to ``.agent-trace/ledgers.jsonl``."""
    d = Path(project_dir) / ".agent-trace"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "ledgers.jsonl", "a") as f:
        f.write(json.dumps(ledger) + "\n")


def load_local_ledgers(project_dir: str) -> dict[str, dict[str, Any]]:
    """Load all ledgers from ``.agent-trace/ledgers.jsonl``.

    Returns a dict keyed by ``commit_sha``.
    """
    ledgers_path = Path(project_dir) / ".agent-trace" / "ledgers.jsonl"
    if not ledgers_path.exists():
        return {}
    ledgers: dict[str, dict[str, Any]] = {}
    try:
        for line in ledgers_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    ledger = json.loads(line)
                    sha = ledger.get("commit_sha", "")
                    if sha:
                        ledgers[sha] = ledger
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return ledgers
