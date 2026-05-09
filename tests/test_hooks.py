"""Tests for hook adapters and registry-backed hooks API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace import hooks
from agent_trace.hooks import claude, codex as codex_mod, cursor


class TestHooksAdapters(unittest.TestCase):
    def test_project_hook_injection_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            self.assertTrue(hooks.configure_cursor_hooks(project_dir=str(repo)))
            self.assertTrue(hooks.configure_cursor_hooks(project_dir=str(repo)))
            cursor_cfg = json.loads((repo / ".cursor" / "hooks.json").read_text())
            body = json.dumps(cursor_cfg)
            self.assertIn("agent-trace record", body)

            self.assertTrue(hooks.configure_claude_hooks(project_dir=str(repo)))
            self.assertTrue(hooks.configure_claude_hooks(project_dir=str(repo)))
            claude_cfg = json.loads((repo / ".claude" / "settings.json").read_text())
            body = json.dumps(claude_cfg)
            self.assertIn("agent-trace record", body)

    def test_global_setup_remove_is_parametrized_over_adapters(self) -> None:
        """Walk every adapter (cursor, claude, codex, ...) generically.

        Each adapter exposes ``global_config_path()`` and ``is_installed()``;
        we patch those paths into a temp HOME so the test is hermetic.
        """
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cursor_global = home / ".cursor" / "hooks.json"
            claude_global = home / ".claude" / "settings.json"
            codex_global = home / ".codex" / "config.toml"

            with (
                patch.object(cursor, "CURSOR_GLOBAL_HOOKS_FILE", cursor_global),
                patch.object(claude, "CLAUDE_GLOBAL_SETTINGS_FILE", claude_global),
                patch.object(codex_mod, "CODEX_DIR", home / ".codex"),
                patch.object(codex_mod, "CODEX_CONFIG_FILE", codex_global),
            ):
                for adapter in hooks.iter_adapters():
                    with self.subTest(tool=adapter.name):
                        result = hooks.setup_global_hooks([adapter.name])
                        self.assertTrue(result[adapter.name])
                        self.assertTrue(adapter.is_installed())
                        removed = hooks.remove_global_hooks([adapter.name])
                        self.assertTrue(removed[adapter.name])
                        self.assertFalse(adapter.is_installed())

    def test_registry_contains_builtin_adapters(self) -> None:
        names = {adapter.name for adapter in hooks.iter_adapters()}
        self.assertIn("cursor", names)
        self.assertIn("claude", names)
        self.assertIn("codex", names)

    def test_adapter_registry_helpers(self) -> None:
        self.assertIsNotNone(hooks.get_adapter("cursor"))
        self.assertIsNotNone(hooks.get_adapter("claude"))
        self.assertIsNotNone(hooks.get_adapter("codex"))
        self.assertIsNone(hooks.get_adapter("nonexistent"))
        self.assertIn("codex", hooks.adapter_names())

    def test_adapters_declare_session_lifecycle_metadata(self) -> None:
        """Session-end / summary classification + env-var lookups are
        registry-driven. ``record.py`` must not name any tool — every
        list comes from the adapters themselves.
        """
        cursor_adapter = hooks.get_adapter("cursor")
        claude_adapter = hooks.get_adapter("claude")
        codex_adapter = hooks.get_adapter("codex")
        assert cursor_adapter and claude_adapter and codex_adapter

        # Cursor: ``afterAgentResponse`` triggers summary only;
        # ``sessionEnd`` triggers summary AND a trace.
        triggers, dispatch = cursor_adapter.is_session_end("afterAgentResponse")
        self.assertTrue(triggers)
        self.assertFalse(dispatch)
        triggers, dispatch = cursor_adapter.is_session_end("sessionEnd")
        self.assertTrue(triggers)
        self.assertTrue(dispatch)

        # Claude: ``Stop`` and ``stop`` trigger summary only.
        for ev in ("Stop", "stop"):
            triggers, dispatch = claude_adapter.is_session_end(ev)
            self.assertTrue(triggers, f"event {ev!r} should be claude session-end")
            self.assertFalse(dispatch)

        # Codex declares no session-end events for now.
        triggers, _ = codex_adapter.is_session_end("CodexTurnComplete")
        self.assertFalse(triggers)

        # Env-var declarations: cursor owns CURSOR_*; claude owns CLAUDE_PROJECT_DIR.
        self.assertIn("CURSOR_TRANSCRIPT_PATH", cursor_adapter.transcript_env_vars)
        self.assertIn("CURSOR_PROJECT_DIR", cursor_adapter.project_dir_env_vars)
        self.assertIn("CLAUDE_PROJECT_DIR", claude_adapter.project_dir_env_vars)

    def test_each_adapter_owns_its_events(self) -> None:
        """An adapter's ``EVENTS`` map is the only place that lists the
        hook event names for that agent. ``record.py``'s dispatcher walks
        the registry, so registering an adapter is sufficient to make its
        events routable.
        """
        cursor_adapter = hooks.get_adapter("cursor")
        claude_adapter = hooks.get_adapter("claude")
        codex_adapter = hooks.get_adapter("codex")
        assert cursor_adapter and claude_adapter and codex_adapter
        self.assertIn("afterFileEdit", cursor_adapter.EVENTS)
        self.assertIn("PostToolUse", claude_adapter.EVENTS)
        self.assertIn("CodexTurnComplete", codex_adapter.EVENTS)
        # No event leaks across adapters.
        cursor_keys = set(cursor_adapter.EVENTS)
        claude_keys = set(claude_adapter.EVENTS)
        codex_keys = set(codex_adapter.EVENTS)
        self.assertFalse(cursor_keys & claude_keys)
        self.assertFalse(cursor_keys & codex_keys)
        self.assertFalse(claude_keys & codex_keys)


class TestRecordDispatcherUsesAdapterMetadata(unittest.TestCase):
    """``record_from_stdin`` must walk the registry for summary triggers,
    pre-summary hooks, and env-var resolution. No adapter name should be
    hardcoded inside ``record.py``.
    """

    def test_pre_summary_hook_runs_then_summary_fires(self) -> None:
        import io

        from agent_trace import record as record_mod

        calls: dict[str, int] = {"pre": 0, "summary": 0}

        # Find any adapter that owns at least one summary_only_event so
        # the test is independent of *which* adapter we exercise.
        adapter = next(
            (a for a in hooks.iter_adapters() if a.summary_only_events),
            None,
        )
        assert adapter is not None, "expected at least one adapter with summary_only_events"
        event_name = adapter.summary_only_events[0]

        original_pre = adapter.pre_summary_hook

        def fake_pre(_data):
            calls["pre"] += 1

        adapter.pre_summary_hook = fake_pre  # type: ignore[method-assign]
        try:
            with (
                patch("sys.stdin", io.StringIO(json.dumps({"hook_event_name": event_name}))),
                patch(
                    "agent_trace.summary.run_session_summary_hook",
                    lambda _d: calls.__setitem__("summary", calls["summary"] + 1),
                ),
            ):
                record_mod.record_from_stdin()
        finally:
            adapter.pre_summary_hook = original_pre  # type: ignore[method-assign]

        self.assertEqual(calls["pre"], 1)
        self.assertEqual(calls["summary"], 1)

    def test_transcript_env_var_falls_back_through_registry(self) -> None:
        import os

        from agent_trace import record as record_mod

        cursor_adapter = hooks.get_adapter("cursor")
        assert cursor_adapter is not None
        var = cursor_adapter.transcript_env_vars[0]
        prev = os.environ.get(var)
        os.environ[var] = "/tmp/some-transcript.jsonl"
        try:
            self.assertEqual(
                record_mod.transcript_path_from_hook({}),
                "/tmp/some-transcript.jsonl",
            )
        finally:
            if prev is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prev


if __name__ == "__main__":
    unittest.main()
