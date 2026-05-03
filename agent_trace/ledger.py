"""
Attribution ledger — deterministic per-line AI attribution at commit time.

Schema 2.0. Built by the post-commit hook immediately after ``git commit``.

Design:
  * Only ``ai`` segments are recorded. Anything not recorded is implicitly
    NO_ATTRIBUTION (we never claim "this was a human" — we only ever assert
    "this matches an AI trace").
  * Attribution is content-driven only: a line is AI if its SHA-256 line
    hash matches a hash in a candidate trace.
  * Two-pass matching:
      1. Primary pass — staging-window-scoped candidates. A trace must have
         ``vcs.revision == parent_sha`` and ``timestamp`` inside the staging
         window. This is the strict, original path; cheap and unambiguous.
      2. Global fallback — for lines still unmatched after the primary pass,
         match against the union of all local traces and all local ledger
         evidence (per-line hashes from previously-built ledgers). This
         recovers attribution under cherry-pick, revert, squash-merge, stash
         pop, and other operations where ``parent_sha`` does not match the
         ``vcs.revision`` recorded in the original trace. Hash equality
         (SHA-256) is the sole criterion — still deterministic.
  * Operation detection: at commit time we look at the commit message for
    cherry-pick (``-x`` trailer) and revert (default message body) signals,
    and at parent count for merges. When detected, the source commit's
    ledger (if reachable locally) is folded into the global pool so its
    hashes are first-class candidates. Recorded as ``derived_from`` on the
    ledger for explainability.
  * Each AI segment carries ``evidence`` — the matched per-line hash + the
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
    h = hashlib.sha256(line.encode("utf-8")).hexdigest()
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
    parents: list[tuple[str, str | None]],
    committed_at: str | None,
) -> list[dict[str, Any]]:
    """Return traces eligible to attribute the current commit.

    ``parents`` is a list of ``(parent_sha, parent_committed_at)`` tuples.
    Empty list means first commit. Multi-element list means merge commit.

    Eligibility (must satisfy all):
      * Trace's ``vcs.revision`` equals **some** parent SHA, or trace has
        no vcs at all (which counts only on a first commit).
      * Trace's ``timestamp`` is strictly after **that specific parent's**
        ``committed_at``. Each parent has its own staging-window lower
        bound — a trace from main's history shouldn't be admitted because
        of feature's later commit time, and vice versa.
      * Trace's ``timestamp`` is no later than ``committed_at`` plus a
        small buffer.
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

    parent_lowers: dict[str, datetime | None] = {
        sha: _parse_iso(ct) for sha, ct in parents
    }
    upper_base = _parse_iso(committed_at)
    upper = (upper_base + timedelta(minutes=10)) if upper_base else None

    candidates: list[dict[str, Any]] = []
    for t in all_traces:
        ts = _parse_iso(t.get("timestamp"))
        if ts is None:
            continue

        vcs = t.get("vcs") or {}
        rev = vcs.get("revision")

        if not parents:
            # First commit — accept traces with no vcs and undefined parent
            # context. (Pre-init traces in a fresh repo.)
            if rev:
                continue
            lower = None
        else:
            if not rev or rev not in parent_lowers:
                continue
            lower = parent_lowers[rev]

        if lower is not None and ts <= lower:
            continue
        if upper is not None and ts > upper:
            continue

        candidates.append(t)

    return candidates


def list_traces_in_staging_window(
    project_dir: str,
    parents: list[tuple[str, str | None]],
    committed_at: str | None,
) -> list[dict[str, Any]]:
    """Return all local trace records in the same eligibility window as
    :func:`build_attribution_ledger`.

    Used for git notes ``all_session_conversations``: every conversation
    URL from traces eligible for this commit, not only lines that ended
    up in the ledger.
    """
    return _find_candidate_traces(project_dir, parents, committed_at)


def _load_all_traces(project_dir: str) -> list[dict[str, Any]]:
    """Load every local trace, with no parent / time filtering.

    Used to seed the global fallback hash index. The primary
    parent-filtered pass runs first; only lines it didn't match are
    rechecked against this pool, so the wider scope cannot override
    correct attributions — it can only fill gaps.
    """
    from .storage import get_traces_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return []
    traces_path = get_traces_path(pid)
    if not traces_path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for raw in traces_path.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


# -------------------------------------------------------------------
# Derived-commit detection (cherry-pick, revert, merge)
# -------------------------------------------------------------------

_CHERRY_PICK_TRAILER_RE = re.compile(
    r"\(cherry picked from commit ([0-9a-f]{7,40})\)", re.IGNORECASE
)
_REVERT_BODY_RE = re.compile(
    r"^This reverts commit ([0-9a-f]{7,40})\.", re.MULTILINE
)


def _detect_derived_from(
    commit_sha: str, parents: list[str], project_dir: str,
) -> dict[str, Any] | None:
    """Inspect HEAD's commit message and parent count to determine whether
    this commit was produced by cherry-pick (``-x``), revert, or merge.

    Returns a dict like ``{"kind": "cherry-pick", "source_sha": "abcd..."}``
    or ``None`` if the commit looks ordinary. ``source_sha`` is resolved to
    a full SHA when possible.
    """
    if len(parents) > 1:
        return {"kind": "merge", "parents": parents}

    msg = _git_raw("log", "-1", "--format=%B", commit_sha, cwd=project_dir) or ""

    m = _CHERRY_PICK_TRAILER_RE.search(msg)
    if m:
        src = m.group(1)
        full = _git("rev-parse", "--verify", src, cwd=project_dir) or src
        return {"kind": "cherry-pick", "source_sha": full}

    m = _REVERT_BODY_RE.search(msg)
    if m:
        src = m.group(1)
        full = _git("rev-parse", "--verify", src, cwd=project_dir) or src
        return {"kind": "revert", "source_sha": full}

    return None


def _ledger_evidence_to_index_entries(
    ledger: dict[str, Any],
    file_path: str,
    alternate_paths: list[str] | None,
    cross_file: bool,
) -> dict[str, dict[str, Any]]:
    """Turn a previously-built ledger's per-line evidence into a hash index
    entry shape (the same shape :func:`_build_trace_hash_index` produces).

    The ledger's segments already carry ``trace_id``, ``model_id``, and
    ``conversation_url``; ``evidence`` carries the per-line hashes. There
    is no ``edit_sequence`` on the ledger side — entries built from ledger
    evidence are tagged with ``edit_sequence=None`` so trace-derived
    entries (which do carry edit sequence) win on tie-break.
    """
    out: dict[str, dict[str, Any]] = {}
    files = ledger.get("files") or {}
    if not isinstance(files, dict):
        return out
    for fp, fl in files.items():
        if not isinstance(fl, dict):
            continue
        if not cross_file and not _trace_file_matches(fp, file_path, alternate_paths):
            continue
        for seg in fl.get("line_attributions", []) or []:
            if not isinstance(seg, dict):
                continue
            if str(seg.get("type", "")).lower() != "ai":
                continue
            tid = seg.get("trace_id") or ""
            entry = {
                "trace_id": tid,
                "model_id": seg.get("model_id"),
                "tool": None,
                "conversation_url": seg.get("conversation_url"),
                "edit_sequence": None,
            }
            for ev in seg.get("evidence", []) or []:
                if not isinstance(ev, dict):
                    continue
                h = ev.get("hash") or ""
                if not h:
                    continue
                # First seen wins; primary trace pool is merged on top later
                # so trace edit_sequence still beats ledger evidence.
                if h not in out:
                    out[h] = entry
    return out


def diff_signature(commit_sha: str, project_dir: str) -> str | None:
    """Return a SHA-256 over the unified diff ``parent..commit_sha`` (across
    every changed file's diff for merge commits, against parent #1 — same
    surface :func:`build_attribution_ledger` consumes).

    Used by post-rewrite to detect whether an amend / rebase changed the
    tree. If the signature differs between old and new SHAs, the existing
    ledger row is stale and must be rebuilt from the new commit.

    Returns ``None`` if the commit is not reachable or the diff cannot be
    computed (e.g. the old SHA was already pruned).
    """
    if not commit_sha:
        return None
    parent = _git("rev-parse", "--verify", f"{commit_sha}^", cwd=project_dir)
    if parent:
        diff = _git_raw("diff", parent, commit_sha, cwd=project_dir)
    else:
        empty_tree = "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18"
        diff = _git_raw("diff", empty_tree, commit_sha, cwd=project_dir)
    if diff is None:
        return None
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _build_global_hash_index(
    project_dir: str,
    file_path: str,
    alternate_paths: list[str] | None,
    *,
    derived_from: dict[str, Any] | None,
    cross_file: bool,
) -> dict[str, dict[str, Any]]:
    """Build the global fallback hash index.

    Sources, in priority order (later sources fill gaps left by earlier ones):
      1. All local traces (file-scoped or cross-file per ``cross_file`` flag).
      2. All local ledger evidence (every previously-built ledger's per-line
         hashes), so cherry-picks / reverts / squashes find attribution
         even when the original traces were pruned.
      3. If a derived commit was detected, the source commit's ledger is
         loaded explicitly — redundant when (2) already includes it, but
         cheap and ensures the source is in scope when ledger evidence is
         skipped (e.g. fresh clone with notes only).

    Trace-derived entries beat ledger-derived entries on hash collision
    because they carry ``edit_sequence`` (latest edit wins).
    """
    all_traces = _load_all_traces(project_dir)
    index = _build_trace_hash_index(
        all_traces, file_path, alternate_paths=alternate_paths, cross_file=cross_file,
    )

    # Add ledger-evidence entries (don't overwrite trace entries).
    ledgers = load_local_ledgers(project_dir)
    for sha, ledger in ledgers.items():
        ev_index = _ledger_evidence_to_index_entries(
            ledger, file_path, alternate_paths, cross_file,
        )
        for h, entry in ev_index.items():
            if h not in index:
                index[h] = entry

    # Explicitly include the source commit of a cherry-pick / revert.
    if derived_from and derived_from.get("kind") in ("cherry-pick", "revert"):
        src_sha = str(derived_from.get("source_sha") or "")
        src_ledger = ledgers.get(src_sha)
        if src_ledger:
            ev_index = _ledger_evidence_to_index_entries(
                src_ledger, file_path, alternate_paths, cross_file,
            )
            for h, entry in ev_index.items():
                if h not in index:
                    index[h] = entry

    return index


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
    """Map ``hash → {trace_id, model_id, tool, conversation_url, edit_sequence}``.

    Full 256-bit SHA-256 hashes give cryptographic uniqueness, so a hash
    match is sufficient for attribution — no content equality guard needed.

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
                            "edit_sequence": edit_seq,
                        }

    return index


# -------------------------------------------------------------------
# Ledger construction
# -------------------------------------------------------------------

def _merge_resolution_lines_per_file(
    parents: list[tuple[str, str | None]],
    commit_sha: str,
    project_dir: str,
) -> dict[str, set[int]]:
    """For a merge commit, return the set of commit-side line numbers in each
    file that are present in ``commit_sha`` but not in **any** parent.

    Algorithm: ``git diff parent_i commit_sha`` per parent gives the set of
    commit-side line numbers added relative to that parent. A line is
    "introduced by the merge resolution" iff it's added relative to **every**
    parent. So we intersect the per-parent line sets per file.

    Files that don't appear in every parent's diff drop out of the result —
    if a file is identical to even one parent, all of its content is
    attributed in that parent's history, not by the merge commit.
    """
    if not parents:
        return {}

    per_parent: list[dict[str, set[int]]] = []
    for sha, _ in parents:
        files_out = _git("diff", "--name-only", sha, commit_sha, cwd=project_dir) or ""
        per_file: dict[str, set[int]] = {}
        for fp in files_out.splitlines():
            fp = fp.strip()
            if not fp:
                continue
            diff = _git_raw("diff", sha, commit_sha, "--", fp, cwd=project_dir)
            lines: set[int] = set()
            if diff:
                for s, e in _parse_diff_ranges(diff):
                    for ln in range(s, e + 1):
                        lines.add(ln)
            per_file[fp] = lines
        per_parent.append(per_file)

    common_files = set(per_parent[0].keys())
    for d in per_parent[1:]:
        common_files &= set(d.keys())

    result: dict[str, set[int]] = {}
    for fp in common_files:
        common_lines = per_parent[0][fp].copy()
        for d in per_parent[1:]:
            common_lines &= d[fp]
        if common_lines:
            result[fp] = common_lines
    return result


def build_attribution_ledger(
    project_dir: str | None = None,
    commit_ref: str = "HEAD",
) -> dict[str, Any] | None:
    """Build a per-line AI-attribution ledger for the current HEAD commit.

    Returns the ledger dict, or None if there is nothing changed.

    Algorithm:
      1. Resolve commit + parent SHAs and their author times.
      2. Detect derived-commit kind (cherry-pick / revert / merge) from
         commit message + parent count.
      3. Find primary candidate traces in the staging window
         (``parent_committed_at`` < trace.timestamp ≤ committed_at + 10min,
         and ``vcs.revision`` matches parent if present).
      4. For each changed file:
         a. Read committed content; compute per-line hash + content.
         b. Build a primary hash index over candidate traces (file-scoped
            first; cross-file fallback only if file-scoped is empty).
         c. PRIMARY pass — for each non-trivial added line, record AI iff
            its line hash matches the primary index. Trivial lines are
            deferred to the fill pass.
         d. FALLBACK pass — for non-trivial lines still unmatched after
            (c), build a global hash index from all local traces and all
            local ledger evidence (file-scoped first; cross-file fallback
            if empty), and try again. Recovers cherry-pick / revert /
            squash / stash-pop attribution. Hash equality only — still
            deterministic.
         e. Fill pass: a trivial line attributed to AI iff both immediate
            non-trivial neighbours are AI of the same trace.
         f. Merge contiguous same-trace lines into segments with evidence.
      5. If anything was attributed, store a ledger; otherwise no ledger.
    """
    if project_dir is None:
        import os
        project_dir = os.getcwd()

    commit_sha = _git("rev-parse", commit_ref, cwd=project_dir)
    if not commit_sha:
        return None

    committed_at = _git("log", "-1", "--format=%aI", commit_sha, cwd=project_dir)

    parents_out = _git(
        "rev-list", "--parents", "-n", "1", commit_sha, cwd=project_dir,
    ) or ""
    parent_shas: list[str] = parents_out.split()[1:] if parents_out else []
    parents: list[tuple[str, str | None]] = []
    for psha in parent_shas:
        pct = _git("log", "-1", "--format=%aI", psha, cwd=project_dir)
        parents.append((psha, pct))

    is_merge = len(parents) > 1
    first_parent_sha = parents[0][0] if parents else None
    first_parent_at = parents[0][1] if parents else None

    derived_from = _detect_derived_from(commit_sha, parent_shas, project_dir)

    # Per-file changed line sets.
    changed_lines_per_file: dict[str, set[int]] = {}
    if is_merge:
        # Only merge-resolution-introduced lines (added vs every parent).
        changed_lines_per_file = _merge_resolution_lines_per_file(
            parents, commit_sha, project_dir,
        )
    elif first_parent_sha:
        files_out = _git(
            "diff", "--name-only", first_parent_sha, commit_sha, cwd=project_dir,
        ) or ""
        for fp in files_out.splitlines():
            fp = fp.strip()
            if not fp:
                continue
            diff = _git_raw(
                "diff", first_parent_sha, commit_sha, "--", fp, cwd=project_dir,
            )
            lines: set[int] = set()
            if diff:
                for s, e in _parse_diff_ranges(diff):
                    for ln in range(s, e + 1):
                        lines.add(ln)
            if lines:
                changed_lines_per_file[fp] = lines
    else:
        # First commit — diff against the empty tree.
        empty_tree = "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18"
        files_out = _git(
            "diff", "--name-only", "--diff-filter=ACMR",
            empty_tree, commit_sha, cwd=project_dir,
        ) or ""
        for fp in files_out.splitlines():
            fp = fp.strip()
            if not fp:
                continue
            diff = _git_raw(
                "diff", empty_tree, commit_sha, "--", fp, cwd=project_dir,
            )
            lines = set()
            if diff:
                for s, e in _parse_diff_ranges(diff):
                    for ln in range(s, e + 1):
                        lines.add(ln)
            if lines:
                changed_lines_per_file[fp] = lines

    if not changed_lines_per_file:
        return None

    rename_map: dict[str, str] = (
        _get_rename_map(first_parent_sha, commit_sha, project_dir)
        if first_parent_sha and not is_merge else {}
    )

    candidates = _find_candidate_traces(project_dir, parents, committed_at)

    used_trace_ids: set[str] = set()
    files_attributions: dict[str, dict[str, Any]] = {}
    used_fallback = False

    for file_path, changed_lines in changed_lines_per_file.items():
        alt_paths: list[str] | None = None
        old_p = rename_map.get(file_path)
        if old_p:
            alt_paths = [old_p]

        file_content = _git_raw(
            "show", f"{commit_sha}:{file_path}", cwd=project_dir,
        )
        if file_content is None:
            continue

        file_lines = _compute_file_lines(file_content)

        # Primary index from staging-window candidates.
        primary_index = _build_trace_hash_index(
            candidates, file_path, alternate_paths=alt_paths,
        )
        if not primary_index:
            primary_index = _build_trace_hash_index(
                candidates, file_path, alternate_paths=alt_paths, cross_file=True,
            )

        line_attr: dict[int, dict[str, Any]] = {}
        trivial: set[int] = set()
        unmatched: list[int] = []

        # PRIMARY pass.
        for ln in sorted(changed_lines):
            if ln < 1 or ln > len(file_lines):
                continue
            content = file_lines[ln - 1]
            if _is_trivial(content):
                trivial.add(ln)
                continue
            h = _line_hash(content)
            entry = primary_index.get(h)
            if entry is None:
                unmatched.append(ln)
                continue
            tid = entry.get("trace_id")
            if not tid:
                unmatched.append(ln)
                continue
            line_attr[ln] = {
                "trace_id": tid,
                "model_id": entry.get("model_id"),
                "conversation_url": entry.get("conversation_url"),
                "hash": h,
                "content": content,
            }
            used_trace_ids.add(tid)

        # FALLBACK pass — only built if there are unmatched non-trivial lines.
        if unmatched:
            global_index = _build_global_hash_index(
                project_dir, file_path, alt_paths,
                derived_from=derived_from, cross_file=False,
            )
            if not global_index:
                global_index = _build_global_hash_index(
                    project_dir, file_path, alt_paths,
                    derived_from=derived_from, cross_file=True,
                )
            for ln in unmatched:
                content = file_lines[ln - 1]
                h = _line_hash(content)
                entry = global_index.get(h)
                if entry is None:
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
                used_fallback = True

        # Fill pass: trivial line is AI iff both neighbours are AI of same trace.
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
        # Nothing attributable. Skip the empty-shell ledger entirely — a
        # non-attributing commit doesn't need a row.
        return None

    ledger: dict[str, Any] = {
        "version": "2.0",
        "commit_sha": commit_sha,
        "parent_sha": first_parent_sha,
        "parent_committed_at": first_parent_at,
        "committed_at": committed_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trace_ids": sorted(used_trace_ids),
        "files": files_attributions,
    }
    if derived_from:
        ledger["derived_from"] = derived_from
    if used_fallback:
        ledger["used_fallback"] = True

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
