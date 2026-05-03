"""
Post-rewrite ledger remapping — updates ledger commit SHAs after rebase/amend.

Git's ``post-rewrite`` hook provides ``old_sha new_sha`` lines on stdin.
For each pair we:

  1. Remap the ledger row's ``commit_sha`` (and ``parent_sha`` if it was
     itself rewritten).
  2. Compare ``diff_signature(old)`` against ``diff_signature(new)``. If
     they differ — i.e., the rewrite changed the tree (amend with edits,
     rebase with conflict resolution, ``rebase --exec`` that mutates
     content) — drop the remapped row and rebuild the ledger from the new
     commit. The line numbers and hashes in the old ledger are stale; the
     deterministic answer for the new commit comes from re-running the
     builder against its actual diff.
  3. Move the corresponding ``refs/notes/agent-trace`` note onto the new
     SHA (removing the orphaned old one). Without this step notes silently
     orphan on unreachable commits after rebase / amend.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _git_silent(*args: str, cwd: str | None = None) -> int:
    """Run a git command, return its exit code; suppress output."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        return r.returncode
    except Exception:
        return 1


def _remove_note(commit_sha: str, repo_dir: str) -> None:
    """Best-effort removal of an agent-trace note on ``commit_sha``.
    Silent if the note doesn't exist."""
    if not commit_sha:
        return
    _git_silent(
        "notes", "--ref", "agent-trace", "remove", "--ignore-missing", commit_sha,
        cwd=repo_dir,
    )


def rewrite_ledgers(project_dir: str | None = None) -> int:
    """After rebase/amend, remap (or rebuild) ledgers and move notes.

    Reads old→new SHA mapping from stdin (git provides this in post-rewrite).
    Returns count of ledger rows touched (remapped + rebuilt + dropped).
    """
    if project_dir is None:
        import os
        project_dir = os.getcwd()

    sha_map: dict[str, str] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            sha_map[parts[0]] = parts[1]

    if not sha_map:
        return 0

    from .ledger import build_attribution_ledger, diff_signature
    from .storage import get_ledgers_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return 0
    ledgers_path = get_ledgers_path(pid)
    if not ledgers_path.exists():
        return 0

    ledgers: list[dict] = []
    try:
        for line in ledgers_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    ledgers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return 0

    # First pass: SHA remap (commit_sha + parent_sha) and bookkeep which
    # rows moved so we can later decide rebuild-vs-keep.
    touched = 0
    moved: list[tuple[str, str, dict]] = []  # (old_sha, new_sha, ledger)
    for ledger in ledgers:
        old_sha = ledger.get("commit_sha", "")
        if old_sha in sha_map:
            new_sha = sha_map[old_sha]
            ledger["commit_sha"] = new_sha
            touched += 1
            moved.append((old_sha, new_sha, ledger))
        old_parent = ledger.get("parent_sha", "")
        if old_parent and old_parent in sha_map:
            ledger["parent_sha"] = sha_map[old_parent]

    if touched == 0:
        return 0

    # Second pass: rebuild rows whose tree-diff changed.
    # `ledgers` may be mutated in-place (rebuild) or have entries removed
    # (rebuild returned None — the rewrite removed all AI lines).
    rebuilt_old_shas: set[str] = set()
    rebuild_failures: set[str] = set()
    rebuilt_replacements: dict[str, dict | None] = {}  # new_sha -> rebuilt ledger or None
    for old_sha, new_sha, _ in moved:
        old_sig = diff_signature(old_sha, project_dir)
        new_sig = diff_signature(new_sha, project_dir)
        if old_sig is None or new_sig is None or old_sig == new_sig:
            continue
        # Tree changed — rebuild deterministically against the new commit.
        try:
            new_ledger = build_attribution_ledger(project_dir, commit_ref=new_sha)
        except Exception:
            rebuild_failures.add(old_sha)
            continue
        rebuilt_old_shas.add(old_sha)
        rebuilt_replacements[new_sha] = new_ledger

    # Rewrite the in-memory list with rebuilt entries swapped in (or dropped
    # when the rebuild produced None).
    if rebuilt_replacements:
        new_list: list[dict] = []
        for ledger in ledgers:
            sha = ledger.get("commit_sha", "")
            if sha in rebuilt_replacements:
                rep = rebuilt_replacements.pop(sha)
                if rep is not None:
                    new_list.append(rep)
                # If rep is None, the rewrite removed all AI lines; drop the row.
            else:
                new_list.append(ledger)
        ledgers = new_list

    try:
        with open(ledgers_path, "w") as f:
            for ledger in ledgers:
                f.write(json.dumps(ledger) + "\n")
    except OSError:
        return 0

    # Third pass: notes. Always drop the old note. Attach a fresh note for
    # rebuilt ledgers; for purely-remapped ones, attach using the (already
    # remapped) ledger. If the rebuild dropped the row, no note is attached.
    try:
        from .git_notes import attach_note_after_ledger

        # Build a quick lookup of current rows by commit_sha (post-rebuild).
        by_sha = {l.get("commit_sha", ""): l for l in ledgers}

        for old_sha, new_sha, _ in moved:
            _remove_note(old_sha, project_dir)
            ledger = by_sha.get(new_sha)
            if ledger is not None:
                attach_note_after_ledger(project_dir, ledger)
    except Exception:
        # Don't let note bookkeeping fail the post-rewrite hook.
        pass

    return touched
