"""Edge-case tests for attribution under non-linear git workflows.

Covers the P0 fixes from ATTRIBUTION_EDGE_CASES.md:
  * Cherry-pick onto a different parent (T3) — global hash-index fallback
    recovers attribution from the source commit's ledger.
  * Revert (T12) — re-added lines attribute back to the original AI trace.
  * Stash-style different-parent edits (T17) — global trace pool covers it.
  * Post-rewrite re-attaches git notes onto the new SHA (T5 / T16).
  * Empty ledger no longer produces a stub note (T23).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_trace import git_notes
from agent_trace.commit_link import create_commit_link
from agent_trace.ledger import build_attribution_ledger, load_local_ledgers
from agent_trace.rewrite import rewrite_ledgers
from agent_trace.storage import (
    ensure_project_dir,
    get_traces_path,
    resolve_project_id,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _run(*args: str, cwd: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=str(cwd), check=True, capture_output=True, text=True,
    )


def _init_repo(repo: Path) -> None:
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.email", "t@e.st", cwd=repo)
    _run("git", "config", "user.name", "t", cwd=repo)
    # Stable commit SHAs aren't needed; just disable signing.
    _run("git", "config", "commit.gpgsign", "false", cwd=repo)


def _commit_all(repo: Path, msg: str) -> str:
    _run("git", "add", "-A", cwd=repo)
    _run("git", "commit", "-m", msg, cwd=repo)
    return _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()


def _line_hash(line: str) -> str:
    return f"sha256:{hashlib.sha256(line.encode('utf-8')).hexdigest()}"


def _write_trace(
    repo: Path,
    *,
    trace_id: str,
    parent_sha: str | None,
    file_path: str,
    lines: list[str],
    timestamp: str | None = None,
    edit_sequence: int = 1,
    model_id: str = "test-model",
    conversation_url: str = "file:///tmp/conv.jsonl",
) -> None:
    """Append a synthesized trace row to the project's traces.jsonl.

    Mirrors the schema written by the real recording path closely enough for
    the ledger builder to consume.
    """
    pid = resolve_project_id(str(repo), create=True)
    assert pid is not None
    ensure_project_dir(pid)
    path = get_traces_path(pid)

    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()

    row = {
        "version": "2.0",
        "id": trace_id,
        "timestamp": timestamp,
        "tool": {"name": "test"},
        "vcs": {"revision": parent_sha} if parent_sha else {},
        "metadata": {"edit_sequence": edit_sequence},
        "files": [
            {
                "path": file_path,
                "conversations": [
                    {
                        "url": conversation_url,
                        "contributor": {"model_id": model_id},
                        "ranges": [
                            {
                                "line_hashes": [
                                    {"line_offset": i, "hash": _line_hash(ln)}
                                    for i, ln in enumerate(lines)
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def _isolated_home():
    """Context-manager-ish: returns a tmpdir to set as AGENT_TRACE_HOME."""
    return tempfile.TemporaryDirectory()


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------


class TestCherryPickAttribution(unittest.TestCase):
    """T3: cherry-pick onto a different parent should still attribute via
    the global hash-index fallback (source commit's local ledger evidence)."""

    def test_cherry_pick_onto_different_parent_recovers_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo_dir:
            os.environ["AGENT_TRACE_HOME"] = home
            try:
                repo = Path(repo_dir)
                _init_repo(repo)

                # Initial commit (baseline parent for branch B).
                (repo / "base.txt").write_text("base\n")
                p0 = _commit_all(repo, "initial")

                # Branch off a feature branch from p0.
                _run("git", "checkout", "-b", "feature", cwd=repo)

                # AI-authored file added on feature branch.
                ai_lines = [
                    "def hello():",
                    "    return 'world'",
                    "",
                    "def goodbye():",
                    "    return 'see ya'",
                ]
                ai_content = "\n".join(ai_lines) + "\n"
                _write_trace(
                    repo,
                    trace_id="trace-feature",
                    parent_sha=p0,
                    file_path="hello.py",
                    lines=ai_lines,
                )
                (repo / "hello.py").write_text(ai_content)
                feature_sha = _commit_all(repo, "feat: add hello")

                # Build the source ledger (post-commit equivalent).
                src_ledger = build_attribution_ledger(str(repo))
                self.assertIsNotNone(src_ledger)
                from agent_trace.ledger import store_ledger_local

                store_ledger_local(src_ledger, str(repo))
                self.assertIn("hello.py", src_ledger["files"])

                # Move main forward with an unrelated commit so its tip differs
                # from feature's parent.
                _run("git", "checkout", "main", cwd=repo)
                (repo / "other.txt").write_text("unrelated\n")
                p1 = _commit_all(repo, "unrelated change on main")
                self.assertNotEqual(p1, p0)

                # Cherry-pick the feature commit (with -x so the source SHA is
                # discoverable from the message). Onto p1, which differs from
                # the feature commit's original parent p0 — the staging-window
                # filter will discard the original trace.
                _run("git", "cherry-pick", "-x", feature_sha, cwd=repo)
                cherry_sha = _run(
                    "git", "rev-parse", "HEAD", cwd=repo,
                ).stdout.strip()
                self.assertNotEqual(cherry_sha, feature_sha)

                # Build the cherry-pick ledger. Should recover attribution
                # via the global fallback (source commit's ledger evidence).
                cp_ledger = build_attribution_ledger(str(repo))
                self.assertIsNotNone(cp_ledger)
                self.assertIn("hello.py", cp_ledger["files"])
                segs = cp_ledger["files"]["hello.py"]["line_attributions"]
                self.assertTrue(segs, "expected at least one AI segment")
                # Original trace ID flows through.
                trace_ids = {s["trace_id"] for s in segs}
                self.assertIn("trace-feature", trace_ids)
                # Operation-detection metadata recorded.
                self.assertEqual(
                    cp_ledger.get("derived_from", {}).get("kind"), "cherry-pick",
                )
                self.assertTrue(cp_ledger.get("used_fallback"))
            finally:
                os.environ.pop("AGENT_TRACE_HOME", None)


class TestRevertAttribution(unittest.TestCase):
    """T12: a revert commit's re-added lines should attribute back to the
    AI trace that originally introduced them, via the global ledger pool."""

    def test_revert_recovers_attribution_for_readded_lines(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo_dir:
            os.environ["AGENT_TRACE_HOME"] = home
            try:
                repo = Path(repo_dir)
                _init_repo(repo)

                # Commit C0 — AI authors hello.py with two functions.
                (repo / "seed.txt").write_text("seed\n")
                p0 = _commit_all(repo, "seed")

                ai_lines = [
                    "def alpha():",
                    "    return 1",
                    "",
                    "def beta():",
                    "    return 2",
                ]
                _write_trace(
                    repo,
                    trace_id="trace-original",
                    parent_sha=p0,
                    file_path="hello.py",
                    lines=ai_lines,
                )
                (repo / "hello.py").write_text("\n".join(ai_lines) + "\n")
                c1 = _commit_all(repo, "feat: ai functions")

                from agent_trace.ledger import store_ledger_local

                led1 = build_attribution_ledger(str(repo))
                self.assertIsNotNone(led1)
                store_ledger_local(led1, str(repo))

                # Commit C2 — human deletes the AI functions entirely.
                (repo / "hello.py").write_text("# all gone\n")
                c2 = _commit_all(repo, "remove ai code")

                # Revert C2 → re-introduces the AI lines.
                _run("git", "revert", "--no-edit", c2, cwd=repo)
                revert_sha = _run(
                    "git", "rev-parse", "HEAD", cwd=repo,
                ).stdout.strip()

                # Build ledger for the revert commit.
                rev_ledger = build_attribution_ledger(str(repo))
                self.assertIsNotNone(rev_ledger)
                self.assertIn("hello.py", rev_ledger["files"])
                segs = rev_ledger["files"]["hello.py"]["line_attributions"]
                trace_ids = {s["trace_id"] for s in segs}
                self.assertIn(
                    "trace-original", trace_ids,
                    "revert should re-attribute to original AI trace",
                )
                self.assertEqual(
                    rev_ledger.get("derived_from", {}).get("kind"), "revert",
                )
            finally:
                os.environ.pop("AGENT_TRACE_HOME", None)


class TestGlobalPoolForOrphanedTraces(unittest.TestCase):
    """T17-style: a trace recorded against a different parent SHA (e.g. via
    stash pop, or just timing) should still be matched by the global pool."""

    def test_stash_like_orphaned_trace_attributes_via_global_pool(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo_dir:
            os.environ["AGENT_TRACE_HOME"] = home
            try:
                repo = Path(repo_dir)
                _init_repo(repo)

                (repo / "seed.txt").write_text("seed\n")
                p0 = _commit_all(repo, "seed")

                # Move the parent forward so any trace anchored to p0 will
                # fail the staging-window filter on the next commit.
                (repo / "other.txt").write_text("other\n")
                p1 = _commit_all(repo, "other")
                self.assertNotEqual(p0, p1)

                # Trace records vcs.revision = p0 — the wrong parent for HEAD.
                ai_lines = ["AI_LINE_ONE", "AI_LINE_TWO"]
                _write_trace(
                    repo,
                    trace_id="trace-stale-parent",
                    parent_sha=p0,
                    file_path="ai.py",
                    lines=ai_lines,
                )

                (repo / "ai.py").write_text("\n".join(ai_lines) + "\n")
                _commit_all(repo, "land ai code")

                ledger = build_attribution_ledger(str(repo))
                self.assertIsNotNone(ledger)
                self.assertIn("ai.py", ledger["files"])
                segs = ledger["files"]["ai.py"]["line_attributions"]
                trace_ids = {s["trace_id"] for s in segs}
                self.assertIn("trace-stale-parent", trace_ids)
                self.assertTrue(ledger.get("used_fallback"))
            finally:
                os.environ.pop("AGENT_TRACE_HOME", None)


class TestPostRewriteReattachesNotes(unittest.TestCase):
    """T5 / T16: after rebase / amend, post-rewrite must move the git note
    to the new SHA (and remove the orphaned old one) so attribution travels
    with the new commit."""

    def test_amend_rewrites_note_to_new_sha(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo_dir:
            os.environ["AGENT_TRACE_HOME"] = home
            try:
                repo = Path(repo_dir)
                _init_repo(repo)

                (repo / "seed.txt").write_text("seed\n")
                p0 = _commit_all(repo, "seed")

                ai_lines = ["AI_AAA", "AI_BBB"]
                _write_trace(
                    repo,
                    trace_id="trace-amend",
                    parent_sha=p0,
                    file_path="x.py",
                    lines=ai_lines,
                )
                (repo / "x.py").write_text("\n".join(ai_lines) + "\n")
                old_sha = _commit_all(repo, "ai code")

                # Build ledger + attach note (mirrors post-commit hook).
                ledger = build_attribution_ledger(str(repo))
                self.assertIsNotNone(ledger)
                from agent_trace.ledger import store_ledger_local

                store_ledger_local(ledger, str(repo))

                # Force notes on so the test exercises the note path.
                from agent_trace.config import get_project_config

                cfg_path = (
                    Path(home) / "projects"
                    / resolve_project_id(str(repo), create=True)
                    / "project-config.json"
                )
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                cfg_path.write_text(json.dumps({"notes": {"enabled": True}}))

                self.assertTrue(
                    git_notes.attach_note_after_ledger(str(repo), ledger),
                )
                self.assertIsNotNone(git_notes.read_note(old_sha, str(repo)))

                # Amend: reword (no tree change).
                _run("git", "commit", "--amend", "-m", "ai code (reworded)", cwd=repo)
                new_sha = _run(
                    "git", "rev-parse", "HEAD", cwd=repo,
                ).stdout.strip()
                self.assertNotEqual(old_sha, new_sha)

                # Drive rewrite_ledgers as the post-rewrite hook would.
                from io import StringIO

                with mock.patch("sys.stdin", StringIO(f"{old_sha} {new_sha}\n")):
                    n = rewrite_ledgers(str(repo))
                self.assertEqual(n, 1)

                # Note now lives on the new SHA, gone from the old.
                self.assertIsNotNone(git_notes.read_note(new_sha, str(repo)))
                self.assertIsNone(git_notes.read_note(old_sha, str(repo)))

                # Ledger row also points at the new SHA.
                ledgers = load_local_ledgers(str(repo))
                self.assertIn(new_sha, ledgers)
                self.assertNotIn(old_sha, ledgers)
            finally:
                os.environ.pop("AGENT_TRACE_HOME", None)


class TestEmptyLedgerSkipsNote(unittest.TestCase):
    """T23: when a commit has no AI-attributed lines, no git note should be
    attached. Empty notes pollute ``git log --show-notes`` and waste space."""

    def test_attach_note_skipped_for_zero_ai_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as repo_dir:
            os.environ["AGENT_TRACE_HOME"] = home
            try:
                repo = Path(repo_dir)
                _init_repo(repo)
                (repo / "f.txt").write_text("hello\n")
                sha = _commit_all(repo, "init")

                empty_ledger = {
                    "version": "2.0",
                    "commit_sha": sha,
                    "parent_sha": None,
                    "committed_at": None,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "trace_ids": [],
                    "files": {},
                }
                # Should refuse to attach.
                self.assertFalse(
                    git_notes.attach_note_after_ledger(str(repo), empty_ledger),
                )
                self.assertIsNone(git_notes.read_note(sha, str(repo)))
            finally:
                os.environ.pop("AGENT_TRACE_HOME", None)


if __name__ == "__main__":
    unittest.main()
