"""End-to-end tests for the full agent-trace tool.

These tests drive the **real** ``agent-trace`` CLI as a subprocess. The
harness:

  * Stands up a fresh ``AGENT_TRACE_HOME`` and a fresh git repo in tmp.
  * Drops a ``agent-trace`` shim on ``PATH`` so the post-commit / post-rewrite
    git hooks installed by ``agent-trace init`` actually fire and call back
    into the same Python that's running the test.
  * Synthesizes Claude-Code ``PostToolUse`` hook events and pipes them
    through ``agent-trace record`` (the same path the real Claude hook
    takes).
  * Lets real ``git commit`` fire the real post-commit hook → real
    ``agent-trace commit-link`` → real ledger build → real notes attach.
  * Asserts on observable output: ``agent-trace blame --json``,
    ``status``, ledger contents on disk.

If a test fails, the assertion message includes the relevant subprocess
stdout + stderr so it's debuggable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# -------------------------------------------------------------------
# Harness
# -------------------------------------------------------------------


class _CLIError(AssertionError):
    pass


_REPO_ROOT = Path(__file__).resolve().parent.parent


class E2EContext:
    """One end-to-end scenario. Use as a context manager."""

    def __init__(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="at-home-"))
        self.parent = Path(tempfile.mkdtemp(prefix="at-repo-parent-"))
        self.repo = self.parent / "repo"
        self.repo.mkdir()
        self.python = sys.executable

        # The venv doesn't have ``agent_trace`` installed in site-packages;
        # it's importable only because the repo root is the cwd at the
        # interactive prompt. For subprocesses we need PYTHONPATH.
        self._pythonpath = (
            str(_REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")
        )

        # Shim so ``agent-trace`` resolved from the post-commit / post-rewrite
        # git hooks lands back in this very Python interpreter, with the
        # in-tree package on sys.path.
        self.bin = self.parent / "bin"
        self.bin.mkdir()
        shim = self.bin / "agent-trace"
        shim.write_text(
            "#!/bin/sh\n"
            f"PYTHONPATH={self._pythonpath} exec {self.python} -m agent_trace.cli \"$@\"\n",
        )
        shim.chmod(0o755)

        # Locked-down env. Strip vars that can confuse git (notably GIT_DIR if
        # the test is run from inside a worktree of the dev's own checkout).
        self.env = {
            **{k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")},
            "AGENT_TRACE_HOME": str(self.home),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": self._pythonpath,
            "HOME": str(self.parent),  # avoid leaking the real ~/.gitconfig
        }

    def __enter__(self) -> "E2EContext":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.parent, ignore_errors=True)

    # -- subprocess plumbing ----------------------------------------

    def cli(
        self,
        *args: str,
        stdin: str | None = None,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.python, "-m", "agent_trace.cli", *args]
        r = subprocess.run(
            cmd,
            cwd=str(cwd or self.repo),
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check and r.returncode != 0:
            raise _CLIError(
                "agent-trace " + " ".join(args)
                + f"\n  rc={r.returncode}\n  stderr:\n{r.stderr}\n  stdout:\n{r.stdout}",
            )
        return r

    def cli_json(self, *args: str, **kw) -> object:
        r = self.cli(*args, **kw)
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise _CLIError(
                f"agent-trace { ' '.join(args) } returned non-JSON stdout:\n{r.stdout}\n"
                f"stderr:\n{r.stderr}\n",
            ) from e

    def git(
        self,
        *args: str,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check and r.returncode != 0:
            raise _CLIError(
                "git " + " ".join(args)
                + f"\n  rc={r.returncode}\n  stderr:\n{r.stderr}\n  stdout:\n{r.stdout}",
            )
        return r

    # -- repo lifecycle ---------------------------------------------

    def git_init(self) -> None:
        self.git("init", "-b", "main")
        self.git("config", "user.email", "t@e.st")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")
        # Disable signing of merge / cherry-pick messages in case the
        # global config insists.
        self.git("config", "tag.gpgsign", "false")

    def init(self) -> None:
        self.cli("init")

    def write(self, path: str, content: str) -> None:
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def read(self, path: str) -> str:
        return (self.repo / path).read_text()

    # -- synthesized Claude hook events -----------------------------

    def claude_write(
        self,
        path: str,
        content: str,
        *,
        session_id: str = "s-default",
        model: str = "claude-sonnet-4-5",
    ) -> None:
        """Pipe a synthesized PostToolUse Write event through `agent-trace record`,
        then write the file to disk so the eventual commit reflects the same
        content the trace claims."""
        absp = str(self.repo / path)
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": absp, "content": content},
            "session_id": session_id,
            "cwd": str(self.repo),
            "model": model,
        }
        self.cli("record", stdin=json.dumps(event))
        self.write(path, content)

    def claude_edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        session_id: str = "s-default",
        model: str = "claude-sonnet-4-5",
    ) -> None:
        """Pipe a synthesized PostToolUse Edit event and update the file on disk."""
        absp = str(self.repo / path)
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": absp,
                "old_string": old_string,
                "new_string": new_string,
            },
            "session_id": session_id,
            "cwd": str(self.repo),
            "model": model,
        }
        # Update the file BEFORE the record event, because Edit ranges are
        # computed against the post-edit file content (matches Claude Code's
        # actual ordering: Claude edits the file, then PostToolUse fires).
        current = (self.repo / path).read_text() if (self.repo / path).exists() else ""
        new_content = current.replace(old_string, new_string, 1)
        self.write(path, new_content)
        self.cli("record", stdin=json.dumps(event))

    # -- git operations & assertions --------------------------------

    def commit(self, msg: str, *paths: str) -> str:
        if paths:
            self.git("add", *paths)
        else:
            self.git("add", "-A")
        self.git("commit", "-m", msg)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def project_id(self) -> str:
        anchor = self.repo / ".git" / "agent-trace-id"
        return anchor.read_text().strip()

    def ledgers(self) -> list[dict]:
        path = self.home / "projects" / self.project_id() / "ledgers.jsonl"
        if not path.is_file():
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
        return out

    def ledger_for(self, sha: str) -> dict | None:
        for led in self.ledgers():
            if led.get("commit_sha") == sha:
                return led
        return None

    def blame_lines(self, path: str, *extra: str) -> list[dict]:
        """Return the JSON blame ``attributions`` list (list of segments)."""
        out = self.cli_json(
            "blame", path, "--json", "--show-no-attribution", *extra,
        )
        if isinstance(out, dict) and "attributions" in out:
            return out["attributions"]
        if isinstance(out, list):
            return out
        raise _CLIError(f"unexpected blame JSON shape: {out!r}")


# -------------------------------------------------------------------
# Helpers used across scenarios
# -------------------------------------------------------------------


def _kinds_by_line(segments: list[dict], file_lines: int) -> dict[int, str]:
    """Project blame segments onto a per-line dict {line: kind}."""
    out: dict[int, str] = {}
    for seg in segments:
        kind = (seg.get("kind") or "").upper()
        s = int(seg.get("start_line", 0))
        e = int(seg.get("end_line", 0))
        for ln in range(s, e + 1):
            if 1 <= ln <= file_lines:
                out[ln] = kind
    # Fill any unattributed line with UNKNOWN
    for ln in range(1, file_lines + 1):
        out.setdefault(ln, "UNKNOWN")
    return out


# -------------------------------------------------------------------
# Scenarios
# -------------------------------------------------------------------


class TestInitAndStatus(unittest.TestCase):
    def test_init_creates_anchor_and_hooks(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("README.md", "# repo\n")
            ctx.git("add", "README.md")
            ctx.git("commit", "-m", "seed")

            ctx.init()

            # Anchor written
            anchor = ctx.repo / ".git" / "agent-trace-id"
            self.assertTrue(anchor.is_file())
            pid = anchor.read_text().strip()
            self.assertTrue(pid.startswith("at-"))

            # Project config dir created
            cfg = ctx.home / "projects" / pid / "project-config.json"
            self.assertTrue(cfg.is_file())

            # Git hooks installed
            for hook in ("post-commit", "post-rewrite"):
                p = ctx.repo / ".git" / "hooks" / hook
                self.assertTrue(p.is_file(), f"{hook} hook missing")
                content = p.read_text()
                self.assertIn("agent-trace", content)
                self.assertTrue(os.access(p, os.X_OK), f"{hook} not executable")

    def test_status_runs_cleanly(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("a.txt", "a\n")
            ctx.git("add", "a.txt")
            ctx.git("commit", "-m", "seed")
            ctx.init()
            r = ctx.cli("status")
            self.assertEqual(r.returncode, 0)
            self.assertIn("project", r.stdout.lower())

    def test_doctor_reports_hooks_configured(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("a.txt", "a\n")
            ctx.git("add", "a.txt")
            ctx.git("commit", "-m", "seed")
            ctx.init()
            r = ctx.cli("doctor", check=False)
            # Doctor may exit non-zero if optional features are missing — we
            # just want to see the hook lines pass.
            joined = (r.stdout + "\n" + r.stderr).lower()
            self.assertIn("post-commit", joined)
            self.assertIn("post-rewrite", joined)


class TestLinearAttribution(unittest.TestCase):
    """Control flow: AI Write, real git commit, post-commit hook builds
    ledger, blame reports AI."""

    def test_single_write_attributes_all_lines(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "seed\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            content = "def hello():\n    return 'world'\n"
            ctx.claude_write("hello.py", content)
            sha = ctx.commit("ai write hello")

            led = ctx.ledger_for(sha)
            self.assertIsNotNone(led, f"ledger missing for {sha}; ledgers={ctx.ledgers()}")
            self.assertIn("hello.py", led["files"])

            segs = ctx.blame_lines("hello.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")

    def test_two_files_two_writes_one_commit(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "seed\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write("a.py", "def a():\n    return 1\n")
            ctx.claude_write("b.py", "def b():\n    return 2\n")
            sha = ctx.commit("two ai files")

            led = ctx.ledger_for(sha)
            self.assertIn("a.py", led["files"])
            self.assertIn("b.py", led["files"])

    def test_ai_edit_followed_by_commit(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            # Pre-commit a human-authored seed file.
            ctx.write("hello.py", "def hello():\n    return 'world'\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "human seed")
            ctx.init()

            # AI now Edits one line.
            ctx.claude_edit("hello.py", "    return 'world'\n", "    return 'WORLD'\n")
            sha = ctx.commit("ai edit")

            led = ctx.ledger_for(sha)
            self.assertIsNotNone(led)
            # Only the changed line should be attributed.
            file_segs = led["files"]["hello.py"]["line_attributions"]
            attributed = set()
            for s in file_segs:
                for ev in s.get("evidence", []):
                    attributed.add(ev["line"])
            self.assertEqual(attributed, {2}, f"expected line 2 attributed, got {attributed}")

    def test_human_only_commit_has_no_ledger(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "seed\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.write("manual.py", "# written by hand\n")
            sha = ctx.commit("human only")

            self.assertIsNone(ctx.ledger_for(sha))
            # And no agent-trace note attached either.
            r = ctx.git("notes", "--ref", "agent-trace", "show", sha, check=False)
            self.assertNotEqual(r.returncode, 0)


class TestCherryPickE2E(unittest.TestCase):
    """Cherry-pick onto a different parent recovers attribution via the
    global hash-index fallback."""

    def test_cherry_pick_different_parent(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            # Branch off, AI authors a file, commit.
            ctx.git("checkout", "-b", "feature")
            content = "def f():\n    return 42\n"
            ctx.claude_write("f.py", content)
            feature_sha = ctx.commit("feat: f")

            # Move main forward independently so its tip != feature's parent.
            ctx.git("checkout", "main")
            ctx.write("other.txt", "main moved\n")
            ctx.commit("main move")

            # Cherry-pick the feature commit (with -x for trailer).
            ctx.git("cherry-pick", "-x", feature_sha)
            cp_sha = ctx.head()
            self.assertNotEqual(cp_sha, feature_sha)

            led = ctx.ledger_for(cp_sha)
            self.assertIsNotNone(
                led, f"ledger missing for cherry-pick commit {cp_sha}",
            )
            self.assertIn("f.py", led["files"])
            self.assertEqual(
                led.get("derived_from", {}).get("kind"), "cherry-pick",
            )

            segs = ctx.blame_lines("f.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")

    def test_cherry_pick_same_parent_works_without_fallback(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.git("checkout", "-b", "feature")
            ctx.claude_write("f.py", "def f():\n    return 1\n")
            feature_sha = ctx.commit("feat")

            # Branch off main (still at original seed) — same parent as feature.
            ctx.git("checkout", "main")
            ctx.git("checkout", "-b", "other")

            ctx.git("cherry-pick", "-x", feature_sha)
            cp_sha = ctx.head()

            led = ctx.ledger_for(cp_sha)
            self.assertIsNotNone(led)
            # Same-parent: primary pass matches, no fallback needed.
            self.assertFalse(led.get("used_fallback", False))


class TestRevertE2E(unittest.TestCase):
    def test_revert_restores_attribution(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ai = "def x():\n    return 'ai-line'\n"
            ctx.claude_write("x.py", ai)
            ctx.commit("ai code")

            # Human deletes the AI file.
            (ctx.repo / "x.py").unlink()
            del_sha = ctx.commit("remove ai code")

            # Revert the deletion.
            ctx.git("revert", "--no-edit", del_sha)
            rev_sha = ctx.head()
            led = ctx.ledger_for(rev_sha)
            self.assertIsNotNone(led)
            self.assertIn("x.py", led["files"])
            self.assertEqual(
                led.get("derived_from", {}).get("kind"), "revert",
            )

            segs = ctx.blame_lines("x.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")


class TestMergeE2E(unittest.TestCase):
    def test_clean_merge_no_false_positives(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            # Feature branch adds b.py (AI).
            ctx.git("checkout", "-b", "feat")
            ctx.claude_write("b.py", "def b():\n    return 'feat'\n")
            ctx.commit("feat AI")

            # Main adds c.py (human).
            ctx.git("checkout", "main")
            ctx.write("c.py", "human\n")
            ctx.commit("main human")

            # Non-FF merge.
            ctx.git("merge", "--no-ff", "-m", "merge feat", "feat")
            merge_sha = ctx.head()

            # Merge commit itself introduces nothing → no ledger row.
            self.assertIsNone(ctx.ledger_for(merge_sha))

            # b.py blame still works via lineage to the feature commit's ledger.
            segs = ctx.blame_lines("b.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")

    def test_merge_with_conflict_resolution_attributes_resolution_line(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            # Common ancestor with conflict.py.
            ctx.write("conflict.py", "base\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "base")
            ctx.init()

            ctx.git("checkout", "-b", "feat")
            ctx.write("conflict.py", "feat-version\n")
            ctx.commit("feat edit")

            ctx.git("checkout", "main")
            ctx.write("conflict.py", "main-version\n")
            ctx.commit("main edit")

            r = ctx.git("merge", "--no-commit", "--no-ff", "feat", check=False)
            self.assertNotEqual(r.returncode, 0)

            # AI resolves the conflict.
            resolution = "RESOLVED_BY_AI\nsecond_resolution_line\n"
            ctx.claude_write("conflict.py", resolution)
            ctx.git("add", "conflict.py")
            ctx.git("commit", "-m", "merge: resolve via AI")

            merge_sha = ctx.head()
            led = ctx.ledger_for(merge_sha)
            self.assertIsNotNone(led, f"merge ledger missing; ledgers={ctx.ledgers()}")
            self.assertIn("conflict.py", led["files"])
            segs = ctx.blame_lines("conflict.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")


class TestSquashE2E(unittest.TestCase):
    def test_squash_merge_recovers_via_global_pool(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            # Feature branch with AI commit. Trace records vcs.revision = seed.
            ctx.git("checkout", "-b", "feat")
            content = "def squash():\n    return 'sq'\n"
            ctx.claude_write("sq.py", content)
            ctx.commit("feat ai")

            # Move main forward independently so the eventual squash commit's
            # parent != feature's parent. This is what makes the global
            # fallback necessary — the staging-window filter would otherwise
            # discard the feature trace.
            ctx.git("checkout", "main")
            ctx.write("other.txt", "main moved\n")
            ctx.commit("main move")

            ctx.git("merge", "--squash", "feat")
            squash_sha = ctx.commit("squash: bring in feat")

            led = ctx.ledger_for(squash_sha)
            self.assertIsNotNone(led)
            # Squash now genuinely relies on the global pool.
            self.assertTrue(led.get("used_fallback", False))
            segs = ctx.blame_lines("sq.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")
            self.assertEqual(kinds[2], "AI")


class TestRebaseAndAmendE2E(unittest.TestCase):
    def test_amend_reword_moves_note(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            # Force notes on so the test exercises the note-move path.
            cfg = ctx.home / "projects" / ctx.project_id() / "project-config.json"
            data = json.loads(cfg.read_text())
            data.setdefault("notes", {})["enabled"] = True
            cfg.write_text(json.dumps(data))

            ctx.claude_write("x.py", "def x():\n    return 1\n")
            old_sha = ctx.commit("ai")

            # Reword amend (no tree change).
            ctx.git("commit", "--amend", "-m", "ai (reworded)")
            new_sha = ctx.head()
            self.assertNotEqual(old_sha, new_sha)

            led_old = ctx.ledger_for(old_sha)
            led_new = ctx.ledger_for(new_sha)
            self.assertIsNone(led_old)
            self.assertIsNotNone(led_new)

            # Note now lives on new SHA, not old.
            r_new = ctx.git("notes", "--ref", "agent-trace", "show", new_sha, check=False)
            r_old = ctx.git("notes", "--ref", "agent-trace", "show", old_sha, check=False)
            self.assertEqual(r_new.returncode, 0)
            self.assertNotEqual(r_old.returncode, 0)

    def test_amend_with_tree_change_rebuilds_ledger(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write("x.py", "def x():\n    return 1\n")
            old_sha = ctx.commit("ai")

            # Amend appends a new AI line (tree change).
            ctx.claude_edit(
                "x.py",
                "def x():\n    return 1\n",
                "def x():\n    return 1\n\ndef y():\n    return 2\n",
            )
            ctx.git("add", "x.py")
            ctx.git("commit", "--amend", "--no-edit")
            new_sha = ctx.head()

            led = ctx.ledger_for(new_sha)
            self.assertIsNotNone(led)
            # All four code lines are AI-attributed (line 3 is blank — handled by fill pass).
            attributed: set[int] = set()
            for s in led["files"]["x.py"]["line_attributions"]:
                for ev in s.get("evidence", []):
                    attributed.add(ev["line"])
            self.assertGreaterEqual(attributed, {1, 2, 4, 5})


class TestStableProjectIdE2E(unittest.TestCase):
    def test_repo_rename_preserves_attribution(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write("x.py", "def x():\n    return 1\n")
            sha = ctx.commit("ai")
            pid_before = ctx.project_id()

            # Rename the repo on disk.
            new_path = ctx.parent / "renamed"
            ctx.repo.rename(new_path)
            ctx.repo = new_path

            pid_after = ctx.project_id()
            self.assertEqual(pid_before, pid_after)

            # Blame still works.
            segs = ctx.blame_lines("x.py")
            kinds = _kinds_by_line(segs, file_lines=2)
            self.assertEqual(kinds[1], "AI")

            # Ledger row still keyed correctly.
            self.assertIsNotNone(ctx.ledger_for(sha))


class TestBlameSurface(unittest.TestCase):
    """The `blame` CLI command's argument surface: --line, --range,
    --json, --require-attribution exit code."""

    def test_blame_line_filter(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write("x.py", "ai_one\nai_two\nai_three\n")
            ctx.commit("ai")

            r = ctx.cli("blame", "x.py", "--line", "2", "--json")
            payload = json.loads(r.stdout)
            segments = payload.get("attributions", payload) if isinstance(payload, dict) else payload
            covered = set()
            for s in segments:
                covered.update(range(int(s["start_line"]), int(s["end_line"]) + 1))
            self.assertEqual(covered, {2})

    def test_blame_range_filter(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write("x.py", "a\nb\nc\nd\ne\n")
            ctx.commit("ai")
            r = ctx.cli("blame", "x.py", "--range", "2-4", "--json")
            payload = json.loads(r.stdout)
            segments = payload.get("attributions", payload) if isinstance(payload, dict) else payload
            covered = set()
            for s in segments:
                covered.update(range(int(s["start_line"]), int(s["end_line"]) + 1))
            self.assertTrue(covered.issubset({2, 3, 4}))
            self.assertTrue(covered)

    def test_require_attribution_exit_code(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            # Human-only file — no traces, no ledger.
            ctx.write("human.py", "def manual():\n    pass\n")
            ctx.commit("human")

            r = ctx.cli("blame", "human.py", "--require-attribution", check=False)
            self.assertNotEqual(r.returncode, 0)


class TestNotesE2E(unittest.TestCase):
    """End-to-end git notes: attribution travels across `git push`/`pull`
    via refs/notes/agent-trace."""

    def test_notes_attached_for_ai_commit(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()
            cfg = ctx.home / "projects" / ctx.project_id() / "project-config.json"
            data = json.loads(cfg.read_text())
            data.setdefault("notes", {})["enabled"] = True
            data["notes"]["include_ledger"] = True
            cfg.write_text(json.dumps(data))

            ctx.claude_write("x.py", "ai_a\nai_b\n")
            sha = ctx.commit("ai")

            r = ctx.git("notes", "--ref", "agent-trace", "show", sha)
            note = json.loads(r.stdout)
            self.assertEqual(note["version"], "2.0")
            self.assertGreaterEqual(note["stats"]["ai_lines"], 2)
            self.assertIn("ledger", note)
            self.assertIn("x.py", note["ledger"]["files"])


class TestMixedAttribution(unittest.TestCase):
    """Multi-trace, multi-session interactions in a single commit."""

    def test_two_sessions_attribute_separately(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            ctx.claude_write(
                "x.py", "from_s1\nfrom_s1_two\n", session_id="s1", model="claude-a",
            )
            # Append from a different session.
            ctx.claude_edit(
                "x.py",
                "from_s1\nfrom_s1_two\n",
                "from_s1\nfrom_s1_two\nfrom_s2_appended\n",
                session_id="s2",
                model="claude-b",
            )
            sha = ctx.commit("two sessions")

            led = ctx.ledger_for(sha)
            self.assertIsNotNone(led)
            segs = led["files"]["x.py"]["line_attributions"]
            trace_ids = {s["trace_id"] for s in segs}
            # Two distinct trace IDs (one per session).
            self.assertGreaterEqual(len(trace_ids), 2)


class TestRebaseE2E(unittest.TestCase):
    """Full rebase flow: post-rewrite remaps multiple ledger rows AND moves
    notes onto each new SHA."""

    def test_rebase_no_tree_change_remaps_ledgers_and_notes(self) -> None:
        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "s\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()
            cfg = ctx.home / "projects" / ctx.project_id() / "project-config.json"
            data = json.loads(cfg.read_text())
            data.setdefault("notes", {})["enabled"] = True
            cfg.write_text(json.dumps(data))

            # Two AI commits on a feature branch.
            ctx.git("checkout", "-b", "feat")
            ctx.claude_write("a.py", "ai_a1\nai_a2\n")
            old_a = ctx.commit("a")
            ctx.claude_write("b.py", "ai_b1\n")
            old_b = ctx.commit("b")

            # Move main forward, then rebase feat onto main.
            ctx.git("checkout", "main")
            ctx.write("c.txt", "main moved\n")
            ctx.commit("main move")
            ctx.git("checkout", "feat")
            ctx.git("rebase", "main")

            # New tip + parent = rebased SHAs.
            new_b = ctx.head()
            new_a = ctx.git("rev-parse", "HEAD^").stdout.strip()
            self.assertNotEqual(new_a, old_a)
            self.assertNotEqual(new_b, old_b)

            # Old ledger rows are gone; new ones present.
            self.assertIsNone(ctx.ledger_for(old_a))
            self.assertIsNone(ctx.ledger_for(old_b))
            self.assertIsNotNone(ctx.ledger_for(new_a))
            self.assertIsNotNone(ctx.ledger_for(new_b))

            # Notes migrated.
            for sha in (new_a, new_b):
                r = ctx.git("notes", "--ref", "agent-trace", "show", sha, check=False)
                self.assertEqual(r.returncode, 0, f"note missing on {sha}")
            for sha in (old_a, old_b):
                r = ctx.git("notes", "--ref", "agent-trace", "show", sha, check=False)
                self.assertNotEqual(r.returncode, 0, f"note still on old {sha}")


if __name__ == "__main__":
    unittest.main()
