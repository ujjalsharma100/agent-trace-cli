"""Tests for conversation enumeration, hashing, and chunked sync (Step 1.3)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_trace.conversations import (
    CHUNK_THRESHOLD_BYTES,
    cache_path_for_sha,
    enumerate_local_blobs,
    hash_file,
    hash_url,
    url_to_local_path,
    write_blob_to_cache,
)
from agent_trace.remote import add_remote
from agent_trace.storage import (
    ensure_project_dir,
    get_ledgers_path,
    get_traces_path,
)
from agent_trace.sync import pull, push


def _seed_trace_with_conversation(pid: str, transcript_path: str) -> None:
    tp = get_traces_path(pid)
    rec = {
        "version": "2.0",
        "id": "trace-1",
        "timestamp": "2026-05-09T00:00:00Z",
        "tool": {"name": "test"},
        "files": [{
            "path": "src/foo.py",
            "conversations": [{
                "contributor": {"type": "ai"},
                "ranges": [{"start_line": 1, "end_line": 1}],
                "url": f"file://{transcript_path}",
            }],
        }],
    }
    tp.write_text(json.dumps(rec) + "\n")


class TestHashing(unittest.TestCase):
    def test_hash_url_deterministic(self):
        self.assertEqual(hash_url("file:///a/b"), hash_url("file:///a/b"))
        self.assertNotEqual(hash_url("file:///a"), hash_url("file:///b"))
        # exact value: sha256 of bytes
        self.assertEqual(
            hash_url("x"),
            hashlib.sha256(b"x").hexdigest(),
        )

    def test_hash_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello world")
            p = f.name
        try:
            self.assertEqual(hash_file(p), hashlib.sha256(b"hello world").hexdigest())
        finally:
            os.unlink(p)

    def test_url_to_local_path(self):
        self.assertEqual(url_to_local_path("file:///tmp/x"), "/tmp/x")
        self.assertIsNone(url_to_local_path("https://example.com/x"))
        self.assertIsNone(url_to_local_path(""))


class TestEnumerateLocalBlobs(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_enumerates_traces_and_ledgers(self):
        # transcript referenced by trace
        t1 = Path(self.tmpdir) / "transcript-a.jsonl"
        t1.write_text("hello\n")
        _seed_trace_with_conversation(self.pid, str(t1))

        # transcript referenced only by ledger
        t2 = Path(self.tmpdir) / "transcript-b.jsonl"
        t2.write_text("world\n")
        ledger = {
            "version": "2.0",
            "commit_sha": "abc",
            "parent_sha": None,
            "committed_at": "2026-05-09T00:00:00Z",
            "created_at": "2026-05-09T00:00:00Z",
            "trace_ids": ["trace-2"],
            "files": {
                "src/bar.py": {
                    "line_attributions": [{
                        "start_line": 1, "end_line": 1, "type": "ai",
                        "trace_id": "trace-2",
                        "conversation_url": f"file://{t2}",
                    }],
                },
            },
        }
        get_ledgers_path(self.pid).write_text(json.dumps(ledger) + "\n")

        blobs = enumerate_local_blobs(self.pid)
        urls = sorted(b.url for b in blobs)
        self.assertEqual(urls, sorted([f"file://{t1}", f"file://{t2}"]))
        for b in blobs:
            self.assertEqual(len(b.content_sha256), 64)
            self.assertGreater(b.size, 0)

    def test_missing_transcript_skipped(self):
        _seed_trace_with_conversation(self.pid, "/nonexistent/path/x.jsonl")
        self.assertEqual(enumerate_local_blobs(self.pid), [])

    def test_non_file_url_skipped(self):
        tp = get_traces_path(self.pid)
        rec = {
            "version": "2.0", "id": "t", "timestamp": "2026-05-09T00:00:00Z",
            "tool": {"name": "test"},
            "files": [{
                "path": "x.py",
                "conversations": [{
                    "contributor": {"type": "ai"},
                    "ranges": [{"start_line": 1, "end_line": 1}],
                    "url": "https://example.com/x",
                }],
            }],
        }
        tp.write_text(json.dumps(rec) + "\n")
        self.assertEqual(enumerate_local_blobs(self.pid), [])

    def test_dedupes_across_traces(self):
        t = Path(self.tmpdir) / "shared.jsonl"
        t.write_text("payload\n")
        # two trace records, same conversation URL
        rec_template = {
            "version": "2.0", "timestamp": "2026-05-09T00:00:00Z",
            "tool": {"name": "test"},
            "files": [{
                "path": "src/foo.py",
                "conversations": [{
                    "contributor": {"type": "ai"},
                    "ranges": [{"start_line": 1, "end_line": 1}],
                    "url": f"file://{t}",
                }],
            }],
        }
        with open(get_traces_path(self.pid), "w") as f:
            for tid in ("a", "b"):
                f.write(json.dumps({**rec_template, "id": tid}) + "\n")

        blobs = enumerate_local_blobs(self.pid)
        self.assertEqual(len(blobs), 1)


class TestWriteBlobToCache(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)

    def tearDown(self):
        self._env_patch.stop()

    def test_round_trip(self):
        data = b"some transcript bytes\n"
        sha = hashlib.sha256(data).hexdigest()
        path = write_blob_to_cache(self.pid, sha, data)
        self.assertEqual(path, cache_path_for_sha(self.pid, sha))
        self.assertEqual(path.read_bytes(), data)

    def test_hash_mismatch_raises(self):
        with self.assertRaises(ValueError):
            write_blob_to_cache(self.pid, "0" * 64, b"actual content")


# -------------------------------------------------------------------
# HTTP-mocked sync tests
# -------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: bytes = b"{}", status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


class TestPushConversationsInline(unittest.TestCase):
    """Small transcripts go inline (no HEAD/blob round trip)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com", token="t")

        self.transcript = Path(self.tmpdir) / "small.jsonl"
        self.transcript.write_text("tiny transcript\n")
        _seed_trace_with_conversation(self.pid, str(self.transcript))

    def tearDown(self):
        self._env_patch.stop()

    def test_inline_push(self):
        captured = {}

        def fake_urlopen(req, *a, **kw):
            url = req.full_url
            if url.endswith("/api/v1/sync/conversations"):
                captured["url"] = url
                captured["body"] = json.loads(req.data.decode())
                return _FakeResp(b'{"ok": true}')
            return _FakeResp(b'{"ok": true}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = push(self.pid, only="conversations")

        self.assertEqual(result.errors, [])
        self.assertEqual(result.conversations_pushed, 1)
        self.assertIn("items", captured["body"])
        item = captured["body"]["items"][0]
        self.assertEqual(item["content"], "tiny transcript\n")
        self.assertEqual(
            item["content_sha256"],
            hashlib.sha256(b"tiny transcript\n").hexdigest(),
        )


class TestPushConversationsChunked(unittest.TestCase):
    """Large transcripts use HEAD then POST blob; pointer-only items follow."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com", token="t")

        self.transcript = Path(self.tmpdir) / "big.jsonl"
        # Make it bigger than the chunk threshold.
        payload = ("x" * 1024 + "\n") * (CHUNK_THRESHOLD_BYTES // 1024 + 4)
        self.transcript.write_text(payload)
        _seed_trace_with_conversation(self.pid, str(self.transcript))
        self.expected_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def tearDown(self):
        self._env_patch.stop()

    def test_head_404_then_post_blob(self):
        calls: list[tuple[str, str, bytes | None]] = []

        def fake_urlopen(req, *a, **kw):
            url = req.full_url
            method = req.get_method()
            calls.append((method, url, req.data))
            if method == "HEAD" and "/blobs/" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=BytesIO(b""))
            if method == "POST" and url.endswith("/api/v1/blobs"):
                return _FakeResp(b"", status=201)
            if method == "POST" and url.endswith("/api/v1/sync/conversations"):
                return _FakeResp(b'{"ok": true}')
            return _FakeResp(b'{}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = push(self.pid, only="conversations")

        self.assertEqual(result.errors, [])
        self.assertEqual(result.conversations_pushed, 1)

        methods = [(m, u) for m, u, _ in calls]
        self.assertTrue(any(m == "HEAD" and self.expected_sha in u for m, u in methods))
        self.assertTrue(any(m == "POST" and u.endswith("/api/v1/blobs") for m, u in methods))
        self.assertTrue(any(m == "POST" and u.endswith("/api/v1/sync/conversations") for m, u in methods))

        # The pointer call carries no inline content.
        sync_call = next(c for c in calls if c[1].endswith("/api/v1/sync/conversations"))
        body = json.loads(sync_call[2].decode())
        self.assertNotIn("content", body["items"][0])
        self.assertEqual(body["items"][0]["content_sha256"], self.expected_sha)

    def test_head_200_skips_blob_post(self):
        calls = []

        def fake_urlopen(req, *a, **kw):
            calls.append((req.get_method(), req.full_url))
            if req.get_method() == "HEAD":
                return _FakeResp(b"", status=200)
            return _FakeResp(b'{"ok": true}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = push(self.pid, only="conversations")

        self.assertEqual(result.errors, [])
        self.assertEqual(result.conversations_pushed, 1)
        # No /api/v1/blobs POST should have happened.
        self.assertFalse(any(m == "POST" and u.endswith("/api/v1/blobs") for m, u in calls))

    def test_blob_endpoint_404_falls_back_to_inline(self):
        """If POST /blobs 404s (legacy server), fall back to inline content."""
        captured = {}

        def fake_urlopen(req, *a, **kw):
            url = req.full_url
            method = req.get_method()
            if method == "HEAD" and "/blobs/" in url:
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=BytesIO(b""))
            if method == "POST" and url.endswith("/api/v1/blobs"):
                raise urllib.error.HTTPError(url, 404, "no such route", hdrs=None, fp=BytesIO(b""))
            if method == "POST" and url.endswith("/api/v1/sync/conversations"):
                captured["body"] = json.loads(req.data.decode())
                return _FakeResp(b'{"ok": true}')
            return _FakeResp(b'{}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = push(self.pid, only="conversations")

        self.assertEqual(result.errors, [])
        self.assertEqual(result.conversations_pushed, 1)
        item = captured["body"]["items"][0]
        # Fell back to inline.
        self.assertIn("content", item)
        self.assertEqual(item["content_sha256"], self.expected_sha)


class TestSyncStateCursor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com", token="t")

        self.transcript = Path(self.tmpdir) / "small.jsonl"
        self.transcript.write_text("hi\n")
        _seed_trace_with_conversation(self.pid, str(self.transcript))

    def tearDown(self):
        self._env_patch.stop()

    def test_cursor_advances_and_dedupes(self):
        def fake_urlopen(req, *a, **kw):
            return _FakeResp(b'{"ok": true}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            r1 = push(self.pid, only="conversations")
            self.assertEqual(r1.conversations_pushed, 1)

            # Second push without changes: cursor blocks the same blob.
            r2 = push(self.pid, only="conversations")
            self.assertEqual(r2.conversations_pushed, 0)


class TestRoundTrip(unittest.TestCase):
    """Push a 2 MB transcript and pull it back byte-identical from a fresh client."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._env_patch = patch.dict(os.environ, {"AGENT_TRACE_HOME": self.tmpdir})
        self._env_patch.start()
        self.pid = "test-project"
        ensure_project_dir(self.pid)
        add_remote(self.pid, "origin", "https://traces.example.com", token="t")

        # 2 MB transcript with deterministic content.
        self.transcript = Path(self.tmpdir) / "big.jsonl"
        chunk = b"".join(
            (json.dumps({"i": i, "msg": "x" * 200}) + "\n").encode("utf-8")
            for i in range(8000)
        )
        self.transcript.write_bytes(chunk)
        self.expected_size = self.transcript.stat().st_size
        self.expected_sha = hashlib.sha256(chunk).hexdigest()
        self.expected_bytes = chunk
        _seed_trace_with_conversation(self.pid, str(self.transcript))

    def tearDown(self):
        self._env_patch.stop()

    def test_push_then_pull_byte_identical(self):
        # Server-side state captured across calls.
        blob_store: dict[str, bytes] = {}
        pointers: list[dict] = []

        def fake_urlopen(req, *a, **kw):
            url = req.full_url
            method = req.get_method()

            # --- Push side ---
            if method == "HEAD" and "/api/v1/blobs/" in url:
                sha = url.rsplit("/", 1)[-1]
                if sha in blob_store:
                    return _FakeResp(b"", status=200)
                raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=BytesIO(b""))
            if method == "POST" and url.endswith("/api/v1/blobs"):
                data = req.data
                sha = hashlib.sha256(data).hexdigest()
                blob_store[sha] = data
                return _FakeResp(b"", status=201)
            if method == "POST" and url.endswith("/api/v1/sync/conversations"):
                body = json.loads(req.data.decode())
                pointers.extend(body["items"])
                return _FakeResp(b'{"ok": true}')

            # --- Pull side ---
            if method == "GET" and "/api/v1/sync/conversations" in url:
                return _FakeResp(json.dumps({"items": pointers}).encode())
            if method == "GET" and "/api/v1/blobs/" in url:
                sha = url.split("/api/v1/blobs/")[-1]
                return _FakeResp(blob_store[sha])

            # The other resource endpoints called by pull/push:
            if method == "POST":
                return _FakeResp(b'{"ok": true}')
            return _FakeResp(b'{"items": []}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            push_result = push(self.pid, full=True)

        self.assertEqual(push_result.errors, [])
        self.assertEqual(push_result.conversations_pushed, 1)
        self.assertIn(self.expected_sha, blob_store)
        self.assertEqual(blob_store[self.expected_sha], self.expected_bytes)

        # Fresh client = different AGENT_TRACE_HOME.
        fresh_home = tempfile.mkdtemp()
        with patch.dict(os.environ, {"AGENT_TRACE_HOME": fresh_home}):
            ensure_project_dir(self.pid)
            add_remote(self.pid, "origin", "https://traces.example.com", token="t")
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                pull_result = pull(self.pid)

            self.assertEqual(pull_result.errors, [])
            self.assertEqual(pull_result.conversations_pulled, 1)

            cached = cache_path_for_sha(self.pid, self.expected_sha)
            self.assertTrue(cached.is_file())
            self.assertEqual(cached.read_bytes(), self.expected_bytes)


if __name__ == "__main__":
    unittest.main()
