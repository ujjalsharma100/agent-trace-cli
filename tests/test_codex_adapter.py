"""Tests for the Codex CLI adapter — hook injection, removal, recording."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace import hooks, record as record_mod, registry as registry_mod
from agent_trace.hooks import codex as codex_mod


FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _git_init_with_commit(repo: str) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    Path(repo, ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


class TestCodexAdapterRegistered(unittest.TestCase):
    def test_codex_appears_in_registry(self) -> None:
        names = {a.name for a in hooks.iter_adapters()}
        self.assertIn("codex", names)

    def test_codex_supports_rules(self) -> None:
        adapter = hooks.get_adapter("codex")
        assert adapter is not None
        self.assertTrue(adapter.supports_rules())
        self.assertEqual(adapter.rule_extension, ".md")
        self.assertEqual(adapter.rules_dir, ".codex/rules")


class TestCodexHookInjection(unittest.TestCase):
    def test_global_inject_writes_notify_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                patch.object(codex_mod, "CODEX_DIR", home / ".codex"),
                patch.object(codex_mod, "CODEX_CONFIG_FILE", home / ".codex" / "config.toml"),
            ):
                results = hooks.setup_global_hooks(["codex"])
                self.assertTrue(results["codex"])
                cfg = (home / ".codex" / "config.toml").read_text()
                self.assertIn("# agent-trace:notify", cfg)
                self.assertIn("agent-trace record", cfg)
                self.assertTrue(hooks.has_global_hooks("codex"))

    def test_inject_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                patch.object(codex_mod, "CODEX_DIR", home / ".codex"),
                patch.object(codex_mod, "CODEX_CONFIG_FILE", home / ".codex" / "config.toml"),
            ):
                hooks.setup_global_hooks(["codex"])
                first = (home / ".codex" / "config.toml").read_text()
                hooks.setup_global_hooks(["codex"])
                second = (home / ".codex" / "config.toml").read_text()
                self.assertEqual(first.count("# agent-trace:notify"), 1)
                self.assertEqual(first, second)

    def test_remove_strips_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with (
                patch.object(codex_mod, "CODEX_DIR", home / ".codex"),
                patch.object(codex_mod, "CODEX_CONFIG_FILE", home / ".codex" / "config.toml"),
            ):
                hooks.setup_global_hooks(["codex"])
                self.assertTrue(hooks.has_global_hooks("codex"))
                removed = hooks.remove_global_hooks(["codex"])
                self.assertTrue(removed["codex"])
                self.assertFalse(hooks.has_global_hooks("codex"))
                cfg = (home / ".codex" / "config.toml").read_text()
                self.assertNotIn("# agent-trace:notify", cfg)

    def test_inject_preserves_existing_unrelated_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg_path = home / ".codex" / "config.toml"
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text('[profile]\nname = "default"\n')
            with (
                patch.object(codex_mod, "CODEX_DIR", home / ".codex"),
                patch.object(codex_mod, "CODEX_CONFIG_FILE", cfg_path),
            ):
                hooks.setup_global_hooks(["codex"])
                cfg = cfg_path.read_text()
                self.assertIn('[profile]', cfg)
                self.assertIn('name = "default"', cfg)
                self.assertIn("# agent-trace:notify", cfg)


class TestCodexEventRecording(unittest.TestCase):
    """Pipe a fake Codex hook event into ``record_from_stdin`` and verify
    the trace is written under ``~/.agent-trace/projects/<id>/traces.jsonl``.
    Mirrors the M0 acceptance criterion for Step 1.2.
    """

    def test_codex_turn_complete_produces_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _git_init_with_commit(tmp)
            import os

            os.environ["AGENT_TRACE_HOME"] = tmp
            try:
                from agent_trace.config import save_project_config
                from agent_trace.storage import (
                    get_traces_path,
                    resolve_project_id,
                )

                with patch.object(registry_mod, "PROJECTS_FILE", Path(tmp) / "projects.json"):
                    pid = resolve_project_id(tmp, create=True)
                    assert pid is not None
                    save_project_config({"project_id": pid, "label": "test"}, project_dir=tmp)

                    payload = json.loads((FIXTURES / "turn_complete.json").read_text())
                    payload["cwd"] = tmp

                    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
                        record_mod.record_from_stdin()

                    traces_path = get_traces_path(pid)
                    self.assertTrue(traces_path.exists(), f"no traces written to {traces_path}")
                    lines = [line for line in traces_path.read_text().splitlines() if line.strip()]
                    self.assertGreaterEqual(len(lines), 1)
                    trace = json.loads(lines[-1])
                    meta = trace.get("metadata") or {}
                    self.assertEqual(meta.get("event"), "turn_complete")
                    self.assertEqual(meta.get("turn_id"), "t-7d9e")
                    self.assertIn(
                        "Refactored auth.py",
                        meta.get("last_assistant_message", ""),
                    )
            finally:
                del os.environ["AGENT_TRACE_HOME"]

    def test_codex_session_start_translator(self) -> None:
        d = json.loads((FIXTURES / "session_start.json").read_text())
        trace, ev = record_mod._codex_SessionStart({**d, "cwd": "/tmp"})
        self.assertEqual(ev, "CodexSessionStart")
        # ``create_trace`` returns None outside a git repo / detached config —
        # we only assert the dispatcher returns a tuple of the right shape.
        self.assertTrue(trace is None or isinstance(trace, dict))


class TestCodexInRecordDispatcher(unittest.TestCase):
    """Codex's events must be discovered via the registry without record.py
    needing any tool-specific knowledge. Verify that owning an event by
    just registering an adapter is sufficient.
    """

    def test_dispatcher_finds_codex_events_via_registry(self) -> None:
        adapter = hooks.get_adapter("codex")
        assert adapter is not None
        self.assertIn("CodexTurnComplete", adapter.EVENTS)
        self.assertIn("CodexSessionStart", adapter.EVENTS)


if __name__ == "__main__":
    unittest.main()
