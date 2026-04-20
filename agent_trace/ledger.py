"""
Attribution ledger — deterministic per-line AI attribution at commit time.

Schema 2.0. Built by the post-commit hook immediately after ``git commit``.

Design:
  * Only ``ai`` segments are recorded. Anything not recorded is implicitly
    NO_ATTRIBUTION (we never claim "this was a human" — we only ever assert
    "this matches an AI trace").
  * Attribution is content-driven only: a line is AI if its SHA-256 line
    hash matches a hash in a candidate trace AND the verbatim content also
    matches (defence against trivial collisions).
  * Range-claim heuristics are gone. Position-based "MIXED" inference was a
    source of false positives (a user-inserted line falling inside the AI's
    original range got marked MIXED with the AI's conversation).
  * Staging window scoping: candidate traces are restricted to those
    recorded *after the parent commit's author time*. Older traces never
    participate, even if their content hashes happen to collide.
  * Each AI segment carries `evidence` — the matched per-line hash + the
    verbatim line content — so attribution can be audited manually.

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


def _get_rename_map(
    parent_sha: str,
    commit_sha: str,
    cwd: str | None = None,
) -> dict[str, str]:
    """Return ``{ new_path: old_path }`` for renames between parent and commit."""
    out = _git_raw("diff", "--find-renames", "--name-status", parent_sha, commit_sha, cwd=cwd)
    if not out:
        return {}
    renames: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        status = parts[0]
        if not status.startswith("R"):
            continue
        old_path, new_path = parts[1], parts[2]
        renames[new_path] = old_path
    return renames


# -------------------------------------------------------------------
# Diff parsing
# -------------------------------------------------------------------

def _parse_diff_ranges(diff_output: str) -> list[tuple[int, int]]:
    """Parse unified diff ``@@`` headers to find added/modified line ranges
    on the *new* side. Returns ``[(start_line, end_line), …]``, 1-indexed."""
    ranges: list[tuple[int, int]] = []
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    in_hunk = False
    current_new_line = 0
    add_start: int | None = None

    for line in diff_output.split("\n"):
        m = hunk_re.match(line)
        if m:
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
            current_new_line = int(m.group(1))
            in_hunk = True
            continue

        if not in_hunk:
            continue

        if line.startswith("\\"):
            continue

        if line.startswith("+"):
            if add_start is None:
                add_start = current_new_line
            current_new_line += 1
        elif line.startswith("-"):
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
        else:
            if add_start is not None:
                ranges.append((add_start, current_new_line - 1))
                add_start = None
            current_new_line += 1

    if add_start is not None:
        ranges.append((add_start, current_new_line - 1))

    return ranges


# -------------------------------------------------------------------
# Line hashing
# -------------------------------------------------------------------

def _line_hash(line: str) -> str:
    h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{h}"


def _compute_file_lines(content: str) -> list[str]:
    """Return file content as a list of lines, 0-indexed (line N → result[N-1])."""
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines


def _is_trivial(line: str) -> bool:
    """Empty / whitespace-only lines collide across all files; treated specially."""
    return line.strip() == ""


# -------------------------------------------------------------------
# Trace candidate selection (staging window)
# -------------------------------------------------------------------

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _find_candidate_traces(
    project_dir: str,
    parent_sha: str | None,
    parent_committed_at: str | None,
    committed_at: str | None,
) -> list[dict[str, Any]]:
    """Return traces eligible to attribute the current commit.

    Eligibility (must satisfy all):
      * Trace's ``vcs.revision`` equals ``parent_sha`` (or no parent — first
        commit), or trace has no vcs at all (recorded outside a git context).
      * Trace's ``timestamp`` is strictly after ``parent_committed_at`` (the
        previous commit's author time). This is the staging window — older
        traces never participate even if their hashes happen to collide.
      * Trace's ``timestamp`` is no later than ``committed_at`` plus a small
        buffer (covers commits made seconds after the trace was recorded).
    """
    from .storage import get_traces_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return []
    traces_path = get_traces_path(pid)
    if not traces_path.exists():
        return []

    all_traces: list[dict[str, Any]] = []
    try:
        for raw in traces_path.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                all_traces.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []

    lower = _parse_iso(parent_committed_at)
    upper_base = _parse_iso(committed_at)
    upper = (upper_base + timedelta(minutes=10)) if upper_base else None

    candidates: list[dict[str, Any]] = []
    for t in all_traces:
        # Revision check
        if parent_sha:
            vcs = t.get("vcs") or {}
            rev = vcs.get("revision")
            if rev and rev != parent_sha:
                continue
        # else first commit — accept any vcs

        # Time window check
        ts = _parse_iso(t.get("timestamp"))
        if ts is None:
            # Reject undated traces — non-determinism risk
            continue
        if lower is not None and ts <= lower:
            continue
        if upper is not None and ts > upper:
            continue

        candidates.append(t)

    return candidates


# -------------------------------------------------------------------
# Trace hash index (with content)
# -------------------------------------------------------------------

def _trace_file_matches(
    fpath: str,
    primary: str,
    alternates: list[str] | None,
) -> bool:
    """Whether trace's file path refers to ``primary`` or an alternate (e.g. pre-rename)."""
    if fpath == primary or fpath.endswith(primary) or primary.endswith(fpath):
        return True
    if alternates:
        for alt in alternates:
            if fpath == alt or fpath.endswith(alt) or alt.endswith(fpath):
                return True
    return False


def _build_trace_hash_index(
    traces: list[dict[str, Any]],
    file_path: str,
    alternate_paths: list[str] | None = None,
    cross_file: bool = False,
) -> dict[str, dict[str, Any]]:
    """Map ``hash → {trace_id, model_id, tool, conversation_url, content, edit_sequence}``.

    Each entry includes the verbatim ``content`` from the trace, used both
    as a collision guard (we require content equality, not just hash) and
    as evidence in the resulting ledger.

    When multiple traces claim the same hash, the one with the highest
    ``edit_sequence`` wins (latest edit takes precedence).
    """
    index: dict[str, dict[str, Any]] = {}

    for trace in traces:
        trace_id = trace.get("id", "")
        meta = trace.get("metadata") or {}
        edit_seq = meta.get("edit_sequence")
        tool = trace.get("tool")

        for fe in trace.get("files", []):
            if not isinstance(fe, dict):
                continue
            fpath = fe.get("path", "")
            if not cross_file and not _trace_file_matches(fpath, file_path, alternate_paths):
                continue

            model_id: str | None = None
            conversation_url: str | None = None
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
                        content = lh.get("content")
                        # Reject hashes recorded without content — schema 2.0
                        # requires content for deterministic match.
                        if content is None:
                            continue

                        existing = index.get(h)
                        if existing is not None:
                            existing_seq = existing.get("edit_sequence")
                            if edit_seq is not None and (
                                existing_seq is None or edit_seq > existing_seq
                            ):
                                pass  # overwrite below
                            else:
                                continue

                        index[h] = {
                            "trace_id": trace_id,
                            "model_id": model_id,
                            "tool": tool,
                            "conversation_url": conversation_url,
                            "content": content,
                            "edit_sequence": edit_seq,
                        }

    return index


# -------------------------------------------------------------------
# Ledger construction
# -------------------------------------------------------------------

def build_attribution_ledger(project_dir: str | None = None) -> dict[str, Any] | None:
    """Build a per-line AI-attribution ledger for the current HEAD commit.

    Returns the ledger dict, or None if there is nothing changed or no
    attributable lines.

    Algorithm:
      1. Resolve commit + parent SHAs and their author times.
      2. Find candidate traces in the staging window
         (``parent_committed_at`` < trace.timestamp ≤ committed_at + 10min,
         and ``vcs.revision`` matches parent if present).
      3. For each changed file:
         a. Read committed content; compute per-line hash + content.
         b. Build a hash index over candidate traces (file-scoped first;
            cross-file fallback only if file-scoped is empty).
         c. For each line in the diff's added range:
              * Skip trivial lines (empty/whitespace) — they're handled in
                a fill pass after.
              * If the line's hash matches an index entry AND the indexed
                content equals the line's content → AI of that trace.
              * Otherwise: do not record (implicit NO_ATTRIBUTION).
         d. Fill pass: a trivial line attributed to AI iff both immediate
            non-trivial neighbours are AI of the same trace.
         e. Merge contiguous same-trace lines into segments with evidence.
    """
    if project_dir is None:
        import os
        project_dir = os.getcwd()

    commit_sha = _git("rev-parse", "HEAD", cwd=project_dir)
    if not commit_sha:
        return None

    parent_sha = _git("rev-parse", "HEAD^", cwd=project_dir)
    committed_at = _git("log", "-1", "--format=%aI", "HEAD", cwd=project_dir)
    parent_committed_at = (
        _git("log", "-1", "--format=%aI", parent_sha, cwd=project_dir)
        if parent_sha else None
    )

    if parent_sha:
        changed_out = _git("diff", "--name-only", "HEAD^", "HEAD", cwd=project_dir)
    else:
        changed_out = _git(
            "diff", "--name-only", "--diff-filter=ACMR",
            "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18", "HEAD",
            cwd=project_dir,
        )
    if not changed_out:
        return None
    changed_files = [f for f in changed_out.splitlines() if f.strip()]
    if not changed_files:
        return None

    rename_map: dict[str, str] = (
        _get_rename_map(parent_sha, commit_sha, project_dir) if parent_sha else {}
    )

    candidates = _find_candidate_traces(
        project_dir, parent_sha, parent_committed_at, committed_at,
    )

    used_trace_ids: set[str] = set()
    files_attributions: dict[str, dict[str, Any]] = {}

    for file_path in changed_files:
        alt_paths: list[str] | None = None
        old_p = rename_map.get(file_path)
        if old_p:
            alt_paths = [old_p]

        file_content = _git_raw("show", f"HEAD:{file_path}", cwd=project_dir)
        if file_content is None:
            continue

        if parent_sha:
            diff_output = _git_raw(
                "diff", "HEAD^", "HEAD", "--", file_path, cwd=project_dir,
            )
        else:
            diff_output = _git_raw(
                "diff", "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18",
                "HEAD", "--", file_path, cwd=project_dir,
            )

        diff_ranges: list[tuple[int, int]] = (
            _parse_diff_ranges(diff_output) if diff_output else []
        )
        if not diff_ranges:
            continue

        changed_lines: set[int] = set()
        for start, end in diff_ranges:
            for ln in range(start, end + 1):
                changed_lines.add(ln)

        file_lines = _compute_file_lines(file_content)

        # File-scoped index first
        hash_index = _build_trace_hash_index(
            candidates, file_path, alternate_paths=alt_paths,
        )
        # Cross-file fallback (still inside staging window)
        if not hash_index:
            hash_index = _build_trace_hash_index(
                candidates, file_path, alternate_paths=alt_paths, cross_file=True,
            )

        # Per-line attribution
        # line_attr[ln] = {"trace_id", "model_id", "conversation_url", "hash", "content"}  (only AI lines)
        # trivial[ln] = True for empty/whitespace lines (handled in fill pass)
        line_attr: dict[int, dict[str, Any]] = {}
        trivial: set[int] = set()

        for ln in sorted(changed_lines):
            if ln < 1 or ln > len(file_lines):
                continue
            content = file_lines[ln - 1]
            if _is_trivial(content):
                trivial.add(ln)
                continue
            h = _line_hash(content)
            entry = hash_index.get(h)
            if entry is None:
                continue
            # Content equality guard against truncated-hash collisions
            if entry.get("content") != content:
                continue
            tid = entry.get("trace_id")
            if not tid:
                continue
            line_attr[ln] = {
                "trace_id": tid,
                "model_id": entry.get("model_id"),
                "conversation_url": entry.get("conversation_url"),
                "hash": h,
                "content": content,
            }
            used_trace_ids.add(tid)

        # Fill pass: trivial line is AI iff both neighbours are AI of same trace
        for ln in sorted(trivial):
            prev = line_attr.get(ln - 1)
            nxt = line_attr.get(ln + 1)
            if prev and nxt and prev["trace_id"] == nxt["trace_id"]:
                line_attr[ln] = {
                    "trace_id": prev["trace_id"],
                    "model_id": prev["model_id"],
                    "conversation_url": prev["conversation_url"],
                    "hash": _line_hash(file_lines[ln - 1]),
                    "content": file_lines[ln - 1],
                }

        if not line_attr:
            continue

        segments = _merge_into_segments(line_attr)
        if segments:
            files_attributions[file_path] = {"line_attributions": segments}

    if not files_attributions:
        # Still produce a ledger record with empty files so the commit is
        # known (no AI lines found). Useful for audit ("we ran, found nothing").
        # But to keep storage tight, only emit when there's at least one trace
        # in the staging window OR something attributable was attempted.
        if not candidates:
            return None

    ledger: dict[str, Any] = {
        "version": "2.0",
        "commit_sha": commit_sha,
        "parent_sha": parent_sha,
        "parent_committed_at": parent_committed_at,
        "committed_at": committed_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trace_ids": sorted(used_trace_ids),
        "files": files_attributions,
    }

    return ledger


def _merge_into_segments(line_attr: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge contiguous lines with the same trace_id into AI segments with evidence."""
    if not line_attr:
        return []

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for ln in sorted(line_attr.keys()):
        la = line_attr[ln]
        evidence_entry = {
            "line": ln,
            "hash": la["hash"],
            "content": la["content"],
        }
        if (
            current is not None
            and current["end_line"] + 1 == ln
            and current["trace_id"] == la["trace_id"]
        ):
            current["end_line"] = ln
            current["evidence"].append(evidence_entry)
        else:
            if current is not None:
                segments.append(current)
            current = {
                "start_line": ln,
                "end_line": ln,
                "type": "ai",
                "trace_id": la["trace_id"],
                "model_id": la.get("model_id"),
                "conversation_url": la.get("conversation_url"),
                "evidence": [evidence_entry],
            }

    if current is not None:
        segments.append(current)

    return segments


# -------------------------------------------------------------------
# Local ledger storage
# -------------------------------------------------------------------

def store_ledger_local(ledger: dict[str, Any], project_dir: str) -> None:
    """Append a ledger to ``<AGENT_TRACE_HOME>/projects/<id>/ledgers.jsonl``."""
    from .storage import ensure_project_dir, get_ledgers_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=True)
    if not pid:
        return
    ensure_project_dir(pid)
    path = get_ledgers_path(pid)
    with open(path, "a") as f:
        f.write(json.dumps(ledger) + "\n")


def load_local_ledgers(project_dir: str) -> dict[str, dict[str, Any]]:
    """Load all ledgers from disk, keyed by ``commit_sha``."""
    from .storage import get_ledgers_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return {}
    ledgers_path = get_ledgers_path(pid)
    if not ledgers_path.exists():
        return {}
    ledgers: dict[str, dict[str, Any]] = {}
    try:
        for line in ledgers_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
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
