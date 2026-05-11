"""
Conversation transcripts: id computation, content-addressed local cache,
enumeration for sync.

Every transcript referenced by a trace is identified by an opaque
``conversation_id`` (sha256 over the original ``file://<abspath>`` URL).
The same id appears in trace records, ledger segments, git notes,
session summaries, and on the wire. The id is stable per
(machine, session) — no raw filesystem paths leak into any record that
travels between machines.

Transcript bytes are snapshotted into a per-project content-addressed
cache at hook time. Both the local machine and every machine that pulls
the conversation read transcript bytes through the same cache layout:
``<project>/conversations/<sha[:2]>/<content_sha256>``.

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


# -------------------------------------------------------------------
# ID + content hashing
# -------------------------------------------------------------------

def compute_conversation_id(transcript_path: str) -> str:
    """Stable opaque id derived from the local transcript path.

    Hashes the ``file://<absolute-path>`` URL form so the same recipe
    is portable across the codebase. The hash is per-(machine, session);
    two engineers never naturally collide on it, and that's correct —
    each transcript is local to its machine.
    """
    if not transcript_path:
        return ""
    return hashlib.sha256(f"file://{transcript_path}".encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path | str, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


# -------------------------------------------------------------------
# Content-addressed cache (uniform on every machine)
# -------------------------------------------------------------------

def get_conversations_cache_dir(project_id: str) -> Path:
    return get_project_dir(project_id) / "conversations"


def cache_path_for_sha(project_id: str, sha256: str) -> Path:
    return get_conversations_cache_dir(project_id) / sha256[:2] / sha256


def cache_has(project_id: str, sha256: str) -> bool:
    return cache_path_for_sha(project_id, sha256).is_file()


def write_blob_to_cache(project_id: str, sha256: str, data: bytes) -> Path:
    """Write ``data`` to the cache, verifying it hashes to ``sha256``.

    Raises ``ValueError`` on hash mismatch.
    """
    actual = hash_bytes(data)
    if actual != sha256:
        raise ValueError(f"sha256 mismatch: expected {sha256}, got {actual}")
    p = cache_path_for_sha(project_id, sha256)
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return p


def read_blob_from_cache(project_id: str, sha256: str) -> bytes | None:
    p = cache_path_for_sha(project_id, sha256)
    try:
        return p.read_bytes()
    except OSError:
        return None


def snapshot_transcript_to_cache(
    project_id: str, transcript_path: str,
) -> tuple[str, int] | None:
    """Read the live transcript file and write its bytes into the cache.

    Returns ``(content_sha256, size)`` or ``None`` if the file can't be read.
    Idempotent: skips the cache write when a matching blob already exists.

    Also updates the per-project ``conversation_id → latest content_sha256``
    index so readers see the most recent snapshot even when later snapshots
    aren't pinned by a trace (e.g., the session-end summary hook captures
    the tail of the conversation after the last tool call).
    """
    if not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    sha = hash_bytes(data)
    size = len(data)
    p = cache_path_for_sha(project_id, sha)
    if not p.is_file():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        except OSError:
            return None
    cid = compute_conversation_id(transcript_path)
    if cid:
        update_conversation_index(project_id, cid, sha)
    return sha, size


# -------------------------------------------------------------------
# conversation_id → latest content_sha256 index (uniform on every machine)
# -------------------------------------------------------------------
#
# The cache is content-addressed, but a given ``conversation_id`` typically
# has multiple cached blobs over the life of a session (the transcript file
# keeps growing). Readers — blame, context, the viewer — want the *latest*
# snapshot, including the tail captured by the session-end summary hook
# that no trace record points at.

def conversation_index_path(project_id: str) -> Path:
    return get_conversations_cache_dir(project_id) / "_index.json"


def read_conversation_index(project_id: str) -> dict[str, str]:
    p = conversation_index_path(project_id)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and k and v
    }


def update_conversation_index(
    project_id: str, conversation_id: str, content_sha256: str,
) -> None:
    """Upsert ``conversation_id → content_sha256``. Last write wins, which
    is exactly what we want — every snapshot in the cache is valid; we just
    point readers at the most recent one."""
    if not conversation_id or not content_sha256:
        return
    p = conversation_index_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    index = read_conversation_index(project_id)
    if index.get(conversation_id) == content_sha256:
        return
    index[conversation_id] = content_sha256
    try:
        p.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass


def latest_sha_for_conversation(
    project_id: str, conversation_id: str,
) -> str | None:
    """Resolve a ``conversation_id`` to its current ``content_sha256`` in the
    local cache, or ``None`` if unknown.

    Auto-seeds the index from trace records the first time it's consulted
    on a project that pre-dates the index (one-shot, safe to re-run — the
    most recent trace-pinned sha wins for each id).
    """
    if not conversation_id:
        return None
    index = read_conversation_index(project_id)
    if not index:
        _seed_index_from_traces(project_id)
        index = read_conversation_index(project_id)
    return index.get(conversation_id) or None


def _seed_index_from_traces(project_id: str) -> None:
    """Walk traces.jsonl once and write the index from the trace-pinned
    pairs we find. Used to rescue data written before the index existed.

    Note: this is a best-effort fallback. Snapshots written by the
    session-end summary hook (which no trace pins) won't appear here, but
    every subsequent snapshot will keep the index current.
    """
    p = conversation_index_path(project_id)
    if p.is_file():
        return
    traces_path = get_traces_path(project_id)
    if not traces_path.is_file():
        return
    latest: dict[str, str] = {}
    try:
        for line in traces_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for cid, sha in _conv_pairs_from_trace(rec):
                latest[cid] = sha
    except OSError:
        return
    if not latest:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
    except OSError:
        pass


# -------------------------------------------------------------------
# Enumeration of pending uploads
# -------------------------------------------------------------------

@dataclass
class ConversationBlob:
    """A (conversation_id, content_sha256) pair pending upload."""

    conversation_id: str
    content_sha256: str
    size: int
    mtime: str  # ISO-8601 UTC; from the cache file's mtime

    def is_chunked(self, threshold: int = CHUNK_THRESHOLD_BYTES) -> bool:
        return self.size > threshold


def _conv_pairs_from_trace(rec: dict) -> Iterator[tuple[str, str]]:
    for fe in rec.get("files", []) or []:
        for conv in fe.get("conversations", []) or []:
            cid = conv.get("id")
            sha = conv.get("content_sha256")
            if isinstance(cid, str) and cid and isinstance(sha, str) and sha:
                yield cid, sha


def _conv_pairs_from_ledger(rec: dict) -> Iterator[tuple[str, str | None]]:
    for _, fa in (rec.get("files") or {}).items():
        for seg in (fa.get("line_attributions") or []):
            cid = seg.get("conversation_id")
            if isinstance(cid, str) and cid:
                yield cid, None


def _iter_conversation_pairs(project_id: str) -> dict[str, str]:
    """Map ``conversation_id → latest content_sha256`` from local records.

    The cross-snapshot index is authoritative: it captures every snapshot
    we've written, including the session-end summary snapshot that no trace
    pins. Traces serve as a fallback when the index is missing (e.g.,
    pre-index data).
    """
    latest: dict[str, str] = dict(read_conversation_index(project_id))

    traces_path = get_traces_path(project_id)
    if traces_path.is_file():
        try:
            for line in traces_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for cid, sha in _conv_pairs_from_trace(rec):
                    latest.setdefault(cid, sha)
        except OSError:
            pass

    # Ledger entries may name conversation_ids that have no trace locally
    # (e.g. pulled from a remote). They contribute no sha pin.
    led_path = get_ledgers_path(project_id)
    if led_path.is_file():
        try:
            for line in led_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for cid, _ in _conv_pairs_from_ledger(rec):
                    latest.setdefault(cid, "")
        except OSError:
            pass

    return latest


def enumerate_local_blobs(project_id: str) -> list[ConversationBlob]:
    """``ConversationBlob`` per (conversation_id, content_sha256) pair whose
    bytes are present in the local cache. Skips ids whose snapshot has not
    yet been materialised (e.g. ledger-only references without a cache hit).
    """
    out: list[ConversationBlob] = []
    for cid, sha in _iter_conversation_pairs(project_id).items():
        if not sha:
            continue
        p = cache_path_for_sha(project_id, sha)
        if not p.is_file():
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append(ConversationBlob(
            conversation_id=cid,
            content_sha256=sha,
            size=st.st_size,
            mtime=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        ))
    return out
