"""
Git post-commit hook logic — links commits to AI traces.

Called by the git post-commit hook (via ``agent-trace commit-link``).
Matches the newly-created commit against traces that were active at
the parent revision, then records the link locally.

Hooks write *only* to local JSONL.  Network sync happens via
``agent-trace push`` / ``agent-trace pull``.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import get_project_config
from .ledger import build_attribution_ledger, store_ledger_local


# -------------------------------------------------------------------
# Git helpers
# -------------------------------------------------------------------

def _git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _get_commit_sha(cwd: str | None = None) -> str | None:
    return _git("rev-parse", "HEAD", cwd=cwd)


def _get_parent_sha(cwd: str | None = None) -> str | None:
    return _git("rev-parse", "HEAD^", cwd=cwd)


def _get_changed_files(cwd: str | None = None) -> list[str]:
    """Files changed between HEAD^ and HEAD."""
    # Handle initial commit (no parent)
    parent = _get_parent_sha(cwd)
    if parent is None:
        # First commit — diff against empty tree
        out = _git("diff", "--name-only", "--diff-filter=ACMR",
                    "4b825dc642cb6eb9a060e54bf899d15f3f4b7b18", "HEAD",
                    cwd=cwd)
    else:
        out = _git("diff", "--name-only", "HEAD^", "HEAD", cwd=cwd)

    if out is None:
        return []
    return [f for f in out.splitlines() if f.strip()]


def _get_commit_date(cwd: str | None = None) -> str | None:
    """Author date of HEAD in ISO-8601 format."""
    return _git("log", "-1", "--format=%aI", "HEAD", cwd=cwd)


# -------------------------------------------------------------------
# Trace matching
# -------------------------------------------------------------------

def _load_local_traces(project_dir: str) -> list[dict]:
    """Load all traces from ``<AGENT_TRACE_HOME>/projects/<id>/traces.jsonl``."""
    from .storage import get_traces_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return []
    traces_path = get_traces_path(pid)
    if not traces_path.exists():
        return []
    traces = []
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


def _trace_matches(trace: dict, parent_sha: str | None, changed_files: set[str]) -> bool:
    """Check if a trace matches the parent revision and touches any changed file."""
    # Must have a VCS revision matching the parent
    vcs = trace.get("vcs", {})
    revision = vcs.get("revision", "")
    if not revision or not parent_sha:
        return False
    if revision != parent_sha:
        return False

    # Must touch at least one changed file
    for fe in trace.get("files", []):
        fpath = fe.get("path", "")
        if fpath in changed_files:
            return True

    return False


def _find_matching_traces_local(
    project_dir: str,
    parent_sha: str | None,
    changed_files: list[str],
) -> list[str]:
    """Find trace IDs that match the parent SHA and touch changed files."""
    if parent_sha is None:
        return []
    traces = _load_local_traces(project_dir)
    changed_set = set(changed_files)
    return [
        t["id"]
        for t in traces
        if t.get("id") and _trace_matches(t, parent_sha, changed_set)
    ]


# -------------------------------------------------------------------
# Storage
# -------------------------------------------------------------------

def _store_local(commit_link: dict, project_dir: str) -> None:
    """Append commit link to ``<AGENT_TRACE_HOME>/projects/<id>/commit-links.jsonl``."""
    from .storage import ensure_project_dir, get_commit_links_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=True)
    if not pid:
        return
    ensure_project_dir(pid)
    path = get_commit_links_path(pid)
    with open(path, "a") as f:
        f.write(json.dumps(commit_link) + "\n")


# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------

def create_commit_link(project_dir: str | None = None) -> dict | None:
    """Create a commit-trace link for the current HEAD commit.

    Algorithm:
      1. git rev-parse HEAD → commit SHA
      2. git rev-parse HEAD^ → parent SHA (handle first commit)
      3. git diff --name-only HEAD^ HEAD → changed files
      4. git log -1 --format=%aI HEAD → commit author date
      5. Find matching traces (local or remote)
      6. Build and store the commit link record

    Returns the commit link dict, or None if no matching traces found.
    """
    if project_dir is None:
        project_dir = os.getcwd()

    commit_sha = _get_commit_sha(project_dir)
    if not commit_sha:
        return None

    parent_sha = _get_parent_sha(project_dir)
    changed_files = _get_changed_files(project_dir)
    committed_at = _get_commit_date(project_dir)

    if not changed_files:
        return None

    # Find matching traces from local storage only.
    trace_ids = _find_matching_traces_local(
        project_dir, parent_sha, changed_files
    )

    # Always build the attribution ledger — even when nothing is AI-attributed.
    ledger = None
    try:
        ledger = build_attribution_ledger(project_dir)
    except Exception:
        pass

    # Store ledger locally regardless of whether traces matched.
    if ledger:
        try:
            store_ledger_local(ledger, project_dir)
        except Exception:
            pass
        try:
            from .git_notes import attach_note_after_ledger

            attach_note_after_ledger(project_dir, ledger)
        except Exception:
            pass

    if not trace_ids:
        return None

    # Build commit link record
    commit_link: dict = {
        "commit_sha": commit_sha,
        "parent_sha": parent_sha,
        "trace_ids": trace_ids,
        "files_changed": changed_files,
        "committed_at": committed_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    _store_local(commit_link, project_dir)
    return commit_link
