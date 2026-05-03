"""Tests for ``agent-trace config`` helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_trace.cli import (
    cmd_config,
    _full_config_snapshot,
    _reset_config_field,
    _set_config_field,
)
from agent_trace.config import (
    get_global_config,
    get_project_config,
    save_global_config,
    save_project_config,
)
from agent_trace.remote import add_remote
from agent_trace.storage import resolve_project_id


class TestConfigCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self._env = patch.dict(os.environ, {"AGENT_TRACE_HOME": str(self.home)})
        self._env.start()
        self._cwd = os.getcwd()
        os.chdir(self.repo)
        save_project_config(
            {
                "notes": {
                    "enabled": True,
                    "include_ledger": True,
                    "include_summary": True,
                    "include_prompts": True,
                    "all_session_conversations": False,
                },
                "summary": {
                    "enabled": True,
                    "command": "cat",
                    "timeout_seconds": 10,
                },
            },
        )
        self.pid = resolve_project_id(str(self.repo), create=False)
        assert self.pid is not None

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._env.stop()
        self.tmp.cleanup()

    def test_show_snapshot_includes_full_config_and_redacts_tokens(self) -> None:
        save_global_config({"auth_token": "top-secret", "tokens": {"origin": "remote-secret"}})
        add_remote(self.pid, "origin", "https://example.com", token="remote-secret")

        snapshot = _full_config_snapshot()

        self.assertEqual(snapshot["project"]["config"]["summary"]["command"], "cat")
        self.assertEqual(snapshot["project"]["remotes"]["origin"]["url"], "https://example.com")
        self.assertEqual(snapshot["global"]["config"]["auth_token"], "(set)")
        self.assertEqual(snapshot["global"]["config"]["tokens"]["origin"], "(set)")

    def test_set_and_reset_project_fields(self) -> None:
        _set_config_field("notes.include-summary", "false")
        _set_config_field("summary.command", "python summarize.py")

        cfg = get_project_config()
        assert cfg is not None
        self.assertFalse(cfg["notes"]["include_summary"])
        self.assertTrue(cfg["summary"]["enabled"])
        self.assertEqual(cfg["summary"]["command"], "python summarize.py")

        _reset_config_field("summary.command")
        _reset_config_field("notes")

        cfg = get_project_config()
        assert cfg is not None
        self.assertNotIn("command", cfg["summary"])
        self.assertEqual(
            cfg["notes"],
            {
                "enabled": True,
                "include_ledger": False,
                "include_summary": True,
                "include_prompts": True,
                "all_session_conversations": False,
            },
        )

    def test_set_and_reset_global_fields(self) -> None:
        _set_config_field("global.auth-token", "secret")
        _set_config_field("global.capture-detached-edits", "yes")
        self.assertEqual(get_global_config()["auth_token"], "secret")
        self.assertTrue(get_global_config()["capture_detached_edits"])

        _reset_config_field("global.auth-token")
        _reset_config_field("global.capture-detached-edits")
        self.assertNotIn("auth_token", get_global_config())
        self.assertNotIn("capture_detached_edits", get_global_config())

    def test_reset_interactive_field_uses_prompted_value(self) -> None:
        args = SimpleNamespace(config_action="reset", field="notes.include-summary", yes=False)
        with patch("agent_trace.cli._prompt", return_value="false"):
            cmd_config(args)
        cfg = get_project_config()
        assert cfg is not None
        self.assertFalse(cfg["notes"]["include_summary"])

    def test_reset_yes_skips_prompts(self) -> None:
        args = SimpleNamespace(config_action="reset", field="summary.command", yes=True)
        with patch("agent_trace.cli._prompt", side_effect=AssertionError("should not prompt")):
            with patch("agent_trace.cli._confirm", side_effect=AssertionError("should not confirm")):
                cmd_config(args)
        cfg = get_project_config()
        assert cfg is not None
        self.assertNotIn("command", cfg.get("summary", {}))


if __name__ == "__main__":
    unittest.main()
