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
    RemoteUrlError,
    _load_remotes,
    _mask_token,
    _save_remotes,
    add_remote,
    get_default_remote,
    get_remote,
    get_remote_base_url,
    get_remote_org_slug,
    get_remote_project_slug,
    get_remote_token,
    list_remotes,
    parse_remote_url,
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


_URL_A = "https://traces.example.com/acme/myrepo"
_URL_B = "https://traces.example.com/acme/other"
_URL_OLD = "https://old.example.com/acme/myrepo"
_URL_NEW = "https://new.example.com/acme/myrepo"


class TestParseRemoteUrl(unittest.TestCase):
    def test_https_with_slug_path(self):
        base, org, proj = parse_remote_url("https://traces.example.com/acme/myrepo")
        self.assertEqual(base, "https://traces.example.com")
        self.assertEqual(org, "acme")
        self.assertEqual(proj, "myrepo")

    def test_http_with_port(self):
        base, org, proj = parse_remote_url("http://localhost:5000/default/myrepo")
        self.assertEqual(base, "http://localhost:5000")
        self.assertEqual(org, "default")
        self.assertEqual(proj, "myrepo")

    def test_trailing_slash_ok(self):
        base, org, proj = parse_remote_url("http://localhost:5000/default/myrepo/")
        self.assertEqual((base, org, proj), ("http://localhost:5000", "default", "myrepo"))

    def test_bare_host_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("http://localhost:5000")

    def test_only_org_in_path_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("http://localhost:5000/acme")

    def test_extra_path_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("http://localhost:5000/acme/myrepo/extra")

    def test_bad_scheme_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("ftp://localhost/acme/myrepo")

    def test_uppercase_slug_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("http://localhost/Acme/myrepo")

    def test_leading_dash_rejected(self):
        with self.assertRaises(RemoteUrlError):
            parse_remote_url("http://localhost/acme/-bad")


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
        entry = add_remote(self.pid, "origin", _URL_A, token="tok123")
        self.assertEqual(entry["url"], _URL_A)
        self.assertEqual(entry["base_url"], "https://traces.example.com")
        self.assertEqual(entry["org_slug"], "acme")
        self.assertEqual(entry["project_slug"], "myrepo")

        remotes = list_remotes(self.pid)
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0]["name"], "origin")
        self.assertEqual(remotes[0]["url"], _URL_A)

        removed = remove_remote(self.pid, "origin")
        self.assertTrue(removed)
        self.assertEqual(list_remotes(self.pid), [])

    def test_add_rejects_bare_url(self):
        with self.assertRaises(ValueError):
            add_remote(self.pid, "origin", "https://traces.example.com")

    def test_add_duplicate_raises(self):
        add_remote(self.pid, "origin", _URL_A)
        with self.assertRaises(ValueError):
            add_remote(self.pid, "origin", _URL_B)

    def test_remove_nonexistent(self):
        self.assertFalse(remove_remote(self.pid, "nope"))

    def test_set_url(self):
        add_remote(self.pid, "origin", _URL_OLD)
        set_remote_url(self.pid, "origin", _URL_NEW)
        r = get_remote(self.pid, "origin")
        self.assertEqual(r["url"], _URL_NEW)
        self.assertEqual(r["base_url"], "https://new.example.com")

    def test_set_url_rejects_bare(self):
        add_remote(self.pid, "origin", _URL_A)
        with self.assertRaises(ValueError):
            set_remote_url(self.pid, "origin", "https://just-host.example.com")

    def test_set_url_nonexistent(self):
        with self.assertRaises(ValueError):
            set_remote_url(self.pid, "nope", _URL_NEW)

    def test_rename(self):
        add_remote(self.pid, "origin", _URL_A)
        rename_remote(self.pid, "origin", "upstream")
        self.assertIsNone(get_remote(self.pid, "origin"))
        self.assertIsNotNone(get_remote(self.pid, "upstream"))

    def test_rename_nonexistent(self):
        with self.assertRaises(ValueError):
            rename_remote(self.pid, "nope", "other")

    def test_rename_collision(self):
        add_remote(self.pid, "origin", _URL_A)
        add_remote(self.pid, "mirror", _URL_B)
        with self.assertRaises(ValueError):
            rename_remote(self.pid, "origin", "mirror")


class TestRemoteAccessors(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_base_url_and_slugs(self):
        add_remote(self.pid, "origin", _URL_A)
        conf = get_remote(self.pid, "origin")
        self.assertEqual(get_remote_base_url(conf), "https://traces.example.com")
        self.assertEqual(get_remote_org_slug(conf), "acme")
        self.assertEqual(get_remote_project_slug(conf), "myrepo")

    def test_legacy_url_only_remote_still_resolves(self):
        # Simulates a remote written before the derived fields existed.
        legacy = {"url": _URL_A}
        self.assertEqual(get_remote_base_url(legacy), "https://traces.example.com")
        self.assertEqual(get_remote_project_slug(legacy), "myrepo")
        self.assertEqual(get_remote_org_slug(legacy), "acme")


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
        add_remote(self.pid, "origin", _URL_A)
        self.assertEqual(get_default_remote(self.pid), "origin")

    def test_no_remotes(self):
        self.assertIsNone(get_default_remote(self.pid))

    def test_multiple_prefers_origin(self):
        add_remote(self.pid, "mirror", _URL_B)
        add_remote(self.pid, "origin", _URL_A)
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
        add_remote(self.pid, "origin", _URL_A, token="super-secret-123")
        info = show_remote(self.pid, "origin")
        self.assertIsNotNone(info)
        self.assertNotIn("super-secret-123", info["token_masked"])
        self.assertEqual(info["base_url"], "https://traces.example.com")
        self.assertEqual(info["project_slug"], "myrepo")
        self.assertEqual(info["org_slug"], "acme")

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
        add_remote(self.pid, "origin", _URL_A, token="secret")
        cfg = json.loads((Path(self.tmpdir) / "config.json").read_text())
        # Token is stored under a per-project scoped key, not the bare name.
        self.assertEqual(cfg["tokens"][f"{self.pid}::origin"], "secret")
        self.assertNotIn("origin", cfg["tokens"])
        r = get_remote(self.pid, "origin")
        self.assertEqual(r["auth"]["token_ref"], f"global:{self.pid}::origin")
        self.assertEqual(get_remote_token(r), "secret")

    def test_two_projects_same_remote_name_do_not_collide(self):
        # Regression: prior to scoping, both projects wrote to
        # ``tokens.origin`` and the second add clobbered the first.
        pid_a = "project-a"
        pid_b = "project-b"
        ensure_project_dir(pid_a)
        ensure_project_dir(pid_b)
        add_remote(pid_a, "origin", _URL_A, token="tokenA")
        add_remote(pid_b, "origin", _URL_B, token="tokenB")

        self.assertEqual(get_remote_token(get_remote(pid_a, "origin")), "tokenA")
        self.assertEqual(get_remote_token(get_remote(pid_b, "origin")), "tokenB")

    def test_remove_remote_drops_global_token(self):
        add_remote(self.pid, "origin", _URL_A, token="secret")
        remove_remote(self.pid, "origin")
        cfg = json.loads((Path(self.tmpdir) / "config.json").read_text())
        self.assertNotIn(f"{self.pid}::origin", cfg.get("tokens", {}))

    def test_legacy_bare_name_token_ref_still_resolves(self):
        # Older versions wrote ``global:origin`` as the ref. Ensure we
        # still resolve those for users who haven't re-set their token.
        cfg_path = Path(self.tmpdir) / "config.json"
        cfg_path.write_text(json.dumps({"tokens": {"origin": "legacy-token"}}))
        self.assertEqual(resolve_token("global:origin"), "legacy-token")

    def test_add_with_env(self):
        add_remote(self.pid, "origin", _URL_A, token_env="TRACE_TOKEN")
        r = get_remote(self.pid, "origin")
        self.assertEqual(r["auth"]["token_ref"], "env:TRACE_TOKEN")

    def test_set_token(self):
        add_remote(self.pid, "origin", _URL_A, token="old")
        set_remote_token(self.pid, "origin", token="new")
        tok = get_remote_token(get_remote(self.pid, "origin"))
        self.assertEqual(tok, "new")


if __name__ == "__main__":
    unittest.main()
