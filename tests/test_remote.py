"""Tests for remote configuration (Phase 3)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_trace import registry as registry_mod
from agent_trace.remote import (
    _load_remotes,
    _mask_token,
    _save_remotes,
    add_remote,
    get_default_remote,
    get_remote,
    get_remote_token,
    list_remotes,
    remove_remote,
    rename_remote,
    resolve_token,
    set_remote_token,
    set_remote_url,
    show_remote,
)
from agent_trace.storage import (
    ensure_project_dir,
    get_agent_trace_home,
    get_project_dir,
)


class TestRemoteCRUD(unittest.TestCase):
    """Round-trip: add → list → show → rename → remove."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_add_list_remove(self):
        entry = add_remote(self.pid, "origin", "https://traces.example.com", token="tok123")
        self.assertEqual(entry["url"], "https://traces.example.com")

        remotes = list_remotes(self.pid)
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0]["name"], "origin")
        self.assertEqual(remotes[0]["url"], "https://traces.example.com")

        removed = remove_remote(self.pid, "origin")
        self.assertTrue(removed)
        self.assertEqual(list_remotes(self.pid), [])

    def test_add_duplicate_raises(self):
        add_remote(self.pid, "origin", "https://example.com")
        with self.assertRaises(ValueError):
            add_remote(self.pid, "origin", "https://other.com")

    def test_remove_nonexistent(self):
        self.assertFalse(remove_remote(self.pid, "nope"))

    def test_set_url(self):
        add_remote(self.pid, "origin", "https://old.com")
        set_remote_url(self.pid, "origin", "https://new.com")
        r = get_remote(self.pid, "origin")
        self.assertEqual(r["url"], "https://new.com")

    def test_set_url_nonexistent(self):
        with self.assertRaises(ValueError):
            set_remote_url(self.pid, "nope", "https://new.com")

    def test_rename(self):
        add_remote(self.pid, "origin", "https://example.com")
        rename_remote(self.pid, "origin", "upstream")
        self.assertIsNone(get_remote(self.pid, "origin"))
        self.assertIsNotNone(get_remote(self.pid, "upstream"))

    def test_rename_nonexistent(self):
        with self.assertRaises(ValueError):
            rename_remote(self.pid, "nope", "other")

    def test_rename_collision(self):
        add_remote(self.pid, "origin", "https://a.com")
        add_remote(self.pid, "mirror", "https://b.com")
        with self.assertRaises(ValueError):
            rename_remote(self.pid, "origin", "mirror")


class TestTokenResolution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_global_token(self):
        cfg_path = Path(self.tmpdir) / "config.json"
        cfg_path.write_text(json.dumps({"tokens": {"origin": "my-secret-token"}}))
        self.assertEqual(resolve_token("global:origin"), "my-secret-token")

    def test_env_token(self):
        with patch.dict(os.environ, {"MY_TOKEN": "env-value"}):
            self.assertEqual(resolve_token("env:MY_TOKEN"), "env-value")

    def test_env_token_missing(self):
        self.assertIsNone(resolve_token("env:NONEXISTENT_VAR_12345"))

    def test_keychain_stub(self):
        self.assertIsNone(resolve_token("keychain:some-name"))

    def test_raw_token(self):
        self.assertEqual(resolve_token("raw-string"), "raw-string")


class TestMaskToken(unittest.TestCase):
    def test_none(self):
        self.assertEqual(_mask_token(None), "(unresolved)")

    def test_short(self):
        self.assertEqual(_mask_token("abc"), "****")

    def test_long(self):
        masked = _mask_token("super-secret-token-1234")
        self.assertTrue(masked.startswith("****"))
        self.assertTrue(masked.endswith("1234"))


class TestDefaultRemote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_single_remote_is_default(self):
        add_remote(self.pid, "origin", "https://example.com")
        self.assertEqual(get_default_remote(self.pid), "origin")

    def test_no_remotes(self):
        self.assertIsNone(get_default_remote(self.pid))

    def test_multiple_prefers_origin(self):
        add_remote(self.pid, "mirror", "https://m.com")
        add_remote(self.pid, "origin", "https://o.com")
        self.assertEqual(get_default_remote(self.pid), "origin")


class TestShowRemote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_show_masks_token(self):
        add_remote(self.pid, "origin", "https://example.com", token="super-secret-123")
        info = show_remote(self.pid, "origin")
        self.assertIsNotNone(info)
        self.assertNotIn("super-secret-123", info["token_masked"])

    def test_show_nonexistent(self):
        self.assertIsNone(show_remote(self.pid, "nope"))


class TestRemoteTokenInRemote(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_add_with_token_stores_globally(self):
        add_remote(self.pid, "origin", "https://example.com", token="secret")
        cfg = json.loads((Path(self.tmpdir) / "config.json").read_text())
        self.assertEqual(cfg["tokens"]["origin"], "secret")
        tok = get_remote_token(get_remote(self.pid, "origin"))
        self.assertEqual(tok, "secret")

    def test_add_with_env(self):
        add_remote(self.pid, "origin", "https://example.com", token_env="TRACE_TOKEN")
        r = get_remote(self.pid, "origin")
        self.assertEqual(r["auth"]["token_ref"], "env:TRACE_TOKEN")

    def test_set_token(self):
        add_remote(self.pid, "origin", "https://example.com", token="old")
        set_remote_token(self.pid, "origin", token="new")
        tok = get_remote_token(get_remote(self.pid, "origin"))
        self.assertEqual(tok, "new")


if __name__ == "__main__":
    unittest.main()
