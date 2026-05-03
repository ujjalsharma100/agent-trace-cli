"""
Post-rewrite ledger remapping — updates ledger commit SHAs after rebase/amend.

Git's ``post-rewrite`` hook provides ``old_sha new_sha`` lines on stdin.
This module reads those mappings, updates ``ledgers.jsonl`` entries, and
re-attaches the corresponding ``refs/notes/agent-trace`` notes onto the new
commit SHAs (removing the orphaned old ones). Without the note step the
notes ref still points at unreachable commits after rebase / amend, so
attribution doesn't travel with ``git push`` for rewritten commits.

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
    """After rebase/amend, remap old commit SHAs to new ones in ledgers and
    move the corresponding git notes to the new SHAs.

    Reads old→new SHA mapping from stdin (git provides this in post-rewrite).
    Updates ``ledgers.jsonl`` entries and re-attaches notes. Returns count
    of remapped ledgers.
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

    remapped = 0
    moved_ledgers: list[dict] = []
    old_shas_to_clear: list[str] = []
    for ledger in ledgers:
        old_sha = ledger.get("commit_sha", "")
        if old_sha in sha_map:
            new_sha = sha_map[old_sha]
            ledger["commit_sha"] = new_sha
            remapped += 1
            moved_ledgers.append(ledger)
            old_shas_to_clear.append(old_sha)
        old_parent = ledger.get("parent_sha", "")
        if old_parent and old_parent in sha_map:
            ledger["parent_sha"] = sha_map[old_parent]

    if remapped == 0:
        return 0

    try:
        with open(ledgers_path, "w") as f:
            for ledger in ledgers:
                f.write(json.dumps(ledger) + "\n")
    except OSError:
        return 0

    # Re-attach notes onto new SHAs and remove the orphaned old ones.
    try:
        from .git_notes import attach_note_after_ledger

        for old_sha in old_shas_to_clear:
            _remove_note(old_sha, project_dir)
        for ledger in moved_ledgers:
            attach_note_after_ledger(project_dir, ledger)
    except Exception:
        # Don't let note bookkeeping fail the post-rewrite hook.
        pass

    return remapped
