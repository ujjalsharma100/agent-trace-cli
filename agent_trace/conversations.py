"""
Conversation transcripts: enumeration and content addressing for sync.

Hooks attach a ``file://`` conversation URL to each trace; the underlying
transcript blob is uploaded separately by ``sync.push()`` so the server
can deduplicate large transcripts via SHA-256 and avoid re-sending bytes
the server already has.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .storage import (
    get_ledgers_path,
    get_project_dir,
    get_traces_path,
)


# Blobs at or under this size go inline in the conversations POST;
# larger blobs go through the chunked HEAD/POST blob path.
CHUNK_THRESHOLD_BYTES = 256 * 1024
FILE_URL_PREFIX = "file://"


# -------------------------------------------------------------------
# Hashing
# -------------------------------------------------------------------

def hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def hash_file(path: Path | str, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def url_to_local_path(url: str) -> str | None:
    if not url or not url.startswith(FILE_URL_PREFIX):
        return None
    return url[len(FILE_URL_PREFIX):]


# -------------------------------------------------------------------
# Local cache (content-addressed)
# -------------------------------------------------------------------

def get_conversations_cache_dir(project_id: str) -> Path:
    """Per-project content-addressed cache for transcripts pulled from a remote."""
    return get_project_dir(project_id) / "conversations"


def cache_path_for_sha(project_id: str, sha256: str) -> Path:
    return get_conversations_cache_dir(project_id) / sha256[:2] / sha256


def write_blob_to_cache(project_id: str, sha256: str, data: bytes) -> Path:
    """Write ``data`` to the cache, verifying it hashes to ``sha256``.

    Raises ``ValueError`` on hash mismatch.
    """
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise ValueError(f"sha256 mismatch: expected {sha256}, got {actual}")
    p = cache_path_for_sha(project_id, sha256)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# -------------------------------------------------------------------
# Enumeration
# -------------------------------------------------------------------

@dataclass
class ConversationBlob:
    url: str
    url_hash: str
    local_path: str
    content_sha256: str
    size: int
    mtime: str  # ISO-8601 UTC

    def is_chunked(self, threshold: int = CHUNK_THRESHOLD_BYTES) -> bool:
        return self.size > threshold


def _conv_urls_from_trace(rec: dict) -> Iterator[str]:
    for fe in rec.get("files", []) or []:
        for conv in fe.get("conversations", []) or []:
            url = conv.get("url")
            if isinstance(url, str) and url:
                yield url


def _conv_urls_from_ledger(rec: dict) -> Iterator[str]:
    for _, fa in (rec.get("files") or {}).items():
        for seg in (fa.get("line_attributions") or []):
            url = seg.get("conversation_url")
            if isinstance(url, str) and url:
                yield url


def _iter_conversation_urls(project_id: str) -> Iterator[str]:
    """Distinct conversation URLs referenced by local traces and ledgers."""
    seen: set[str] = set()
    for path, extractor in (
        (get_traces_path(project_id), _conv_urls_from_trace),
        (get_ledgers_path(project_id), _conv_urls_from_ledger),
    ):
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for u in extractor(rec):
                if u not in seen:
                    seen.add(u)
                    yield u


def enumerate_local_blobs(project_id: str) -> list[ConversationBlob]:
    """``ConversationBlob`` for every conversation URL whose transcript is
    readable on the local filesystem.

    Skips URLs we can't resolve (non-``file://``, missing on disk, unreadable).
    """
    out: list[ConversationBlob] = []
    for url in _iter_conversation_urls(project_id):
        local = url_to_local_path(url)
        if local is None:
            continue
        try:
            st = os.stat(local)
        except OSError:
            continue
        try:
            sha = hash_file(local)
        except OSError:
            continue
        out.append(ConversationBlob(
            url=url,
            url_hash=hash_url(url),
            local_path=local,
            content_sha256=sha,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        ))
    return out
