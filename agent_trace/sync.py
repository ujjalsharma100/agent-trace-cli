"""
Push/Pull sync protocol — explicit git-like sync between local and remote.

Hooks write only to local JSONL.  ``push()`` and ``pull()`` are the sole
network callers.

No external dependencies — stdlib only (urllib for HTTP).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conversations import (
    CHUNK_THRESHOLD_BYTES,
    ConversationBlob,
    cache_path_for_sha,
    enumerate_local_blobs,
    write_blob_to_cache,
)
from .remote import get_remote_token, get_remote_url, resolve_remote
from .storage import (
    ensure_project_dir,
    get_commit_links_path,
    get_ledgers_path,
    get_project_dir,
    get_traces_path,
)


# -------------------------------------------------------------------
# Sync state persistence
# -------------------------------------------------------------------

def _sync_state_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "sync-state.json"


def _load_sync_state(project_id: str) -> dict[str, Any]:
    p = _sync_state_path(project_id)
    if not p.is_file():
        return {"remotes": {}}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {"remotes": {}}
    except (json.JSONDecodeError, OSError):
        return {"remotes": {}}


def _save_sync_state(project_id: str, state: dict[str, Any]) -> None:
    ensure_project_dir(project_id)
    p = _sync_state_path(project_id)
    p.write_text(json.dumps(state, indent=2) + "\n")


# -------------------------------------------------------------------
# Local JSONL readers
# -------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _read_traces(project_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(get_traces_path(project_id))


def _read_ledgers(project_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(get_ledgers_path(project_id))


def _read_commit_links(project_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(get_commit_links_path(project_id))


# -------------------------------------------------------------------
# Attribution filter
# -------------------------------------------------------------------

def compute_attributed_trace_ids(project_id: str) -> set[str]:
    """Set of trace IDs referenced by at least one local ledger."""
    result: set[str] = set()
    for ledger in _read_ledgers(project_id):
        for tid in ledger.get("trace_ids", []):
            result.add(tid)
    return result


# -------------------------------------------------------------------
# HTTP helpers
# -------------------------------------------------------------------

def _http_post(url: str, body: Any, token: str | None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _http_get(url: str, token: str | None, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _http_get_bytes(url: str, token: str | None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_head_status(url: str, token: str | None, timeout: int = 15) -> int:
    """Return the HTTP status code of a HEAD request (or the HTTPError code)."""
    req = urllib.request.Request(url, method="HEAD")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _http_post_bytes(
    url: str, body: bytes, token: str | None, *, content_type: str = "application/octet-stream", timeout: int = 60,
) -> int:
    """POST a raw byte body. Returns status code; raises HTTPError on >=400."""
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


# -------------------------------------------------------------------
# Push
# -------------------------------------------------------------------

@dataclass
class PushResult:
    traces_pushed: int = 0
    ledgers_pushed: int = 0
    commit_links_pushed: int = 0
    conversations_pushed: int = 0
    traces_held_back: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def push(
    project_id: str,
    remote_name: str | None = None,
    *,
    full: bool = False,
    only: str | None = None,
    since: str | None = None,
    dry_run: bool = False,
) -> PushResult:
    """Push local data to a remote service."""
    result = PushResult(dry_run=dry_run)

    rname, rconf = resolve_remote(project_id, remote_name)
    base_url = get_remote_url(rconf).rstrip("/")
    token = get_remote_token(rconf)

    if not base_url:
        result.errors.append("Remote has no URL configured.")
        return result

    attributed_ids = compute_attributed_trace_ids(project_id) if not full else None

    # Load sync state
    sync_state = _load_sync_state(project_id)
    remote_state = sync_state.setdefault("remotes", {}).setdefault(rname, {})
    last_push = remote_state.get("last_push", {})

    # --- Traces ---
    if only is None or only == "traces":
        traces = _read_traces(project_id)
        traces_since = last_push.get("traces_max_timestamp")
        to_push = []
        held_back = 0
        for t in traces:
            ts = t.get("timestamp", "")
            if traces_since and ts <= traces_since:
                continue
            if since and ts < since:
                continue
            tid = t.get("id", "")
            if attributed_ids is not None and tid not in attributed_ids:
                held_back += 1
                continue
            to_push.append(t)

        result.traces_held_back = held_back

        if to_push and not dry_run:
            try:
                _http_post(
                    f"{base_url}/api/v1/sync/traces",
                    {"project_id": project_id, "items": to_push},
                    token,
                )
                result.traces_pushed = len(to_push)
                max_ts = max((t.get("timestamp", "") for t in to_push), default=None)
                if max_ts:
                    last_push["traces_max_timestamp"] = max_ts
            except Exception as e:
                result.errors.append(f"traces: {e}")
        elif to_push:
            result.traces_pushed = len(to_push)

    # --- Ledgers ---
    if only is None or only == "ledgers":
        ledgers = _read_ledgers(project_id)
        ledgers_since = last_push.get("ledgers_max_commit_at")
        to_push_l = []
        for led in ledgers:
            ca = led.get("committed_at") or led.get("created_at", "")
            if ledgers_since and ca <= ledgers_since:
                continue
            if since and ca < since:
                continue
            to_push_l.append(led)

        if to_push_l and not dry_run:
            try:
                _http_post(
                    f"{base_url}/api/v1/sync/ledgers",
                    {"project_id": project_id, "items": to_push_l},
                    token,
                )
                result.ledgers_pushed = len(to_push_l)
                max_ca = max(
                    (l.get("committed_at") or l.get("created_at", "") for l in to_push_l),
                    default=None,
                )
                if max_ca:
                    last_push["ledgers_max_commit_at"] = max_ca
            except Exception as e:
                result.errors.append(f"ledgers: {e}")
        elif to_push_l:
            result.ledgers_pushed = len(to_push_l)

    # --- Commit links ---
    if only is None or only == "commit-links":
        links = _read_commit_links(project_id)
        links_since = last_push.get("commit_links_max_commit_at")
        to_push_c = []
        for cl in links:
            ca = cl.get("committed_at") or cl.get("created_at", "")
            if links_since and ca <= links_since:
                continue
            if since and ca < since:
                continue
            to_push_c.append(cl)

        if to_push_c and not dry_run:
            try:
                _http_post(
                    f"{base_url}/api/v1/sync/commit-links",
                    {"project_id": project_id, "items": to_push_c},
                    token,
                )
                result.commit_links_pushed = len(to_push_c)
                max_ca = max(
                    (c.get("committed_at") or c.get("created_at", "") for c in to_push_c),
                    default=None,
                )
                if max_ca:
                    last_push["commit_links_max_commit_at"] = max_ca
            except Exception as e:
                result.errors.append(f"commit-links: {e}")
        elif to_push_c:
            result.commit_links_pushed = len(to_push_c)

    # --- Conversations (chunked, hash-addressed) ---
    if only is None or only == "conversations":
        _push_conversations(
            project_id=project_id,
            base_url=base_url,
            token=token,
            last_push=last_push,
            since=since,
            full=full,
            dry_run=dry_run,
            result=result,
        )

    # Update sync state
    if not dry_run:
        remote_state["last_push"] = last_push
        _save_sync_state(project_id, sync_state)

    return result


def _push_conversations(
    *,
    project_id: str,
    base_url: str,
    token: str | None,
    last_push: dict[str, Any],
    since: str | None,
    full: bool,
    dry_run: bool,
    result: PushResult,
) -> None:
    """Push transcript blobs and pointers, deduping by SHA-256.

    Protocol per blob:
      - Inline (size <= CHUNK_THRESHOLD_BYTES): include ``content`` directly
        in the conversations POST.
      - Chunked: ``HEAD /api/v1/blobs/<sha>``. If 200, blob is already on
        the server — send a pointer only. If 404, ``POST /api/v1/blobs``
        with the raw bytes, then send a pointer. If the blob endpoints are
        not implemented (404/405 on POST), fall back to inline upload for
        the rest of this run so older services keep working.
    """
    blobs = enumerate_local_blobs(project_id)
    cursor = last_push.get("conversations_max_updated_at")

    pending: list[ConversationBlob] = []
    for b in blobs:
        if not full and cursor and b.mtime <= cursor:
            continue
        if since and b.mtime < since:
            continue
        pending.append(b)

    if not pending:
        return

    if dry_run:
        result.conversations_pushed = len(pending)
        return

    items: list[dict[str, Any]] = []
    chunked_supported = True
    pushed_mtimes: list[str] = []

    for b in pending:
        item: dict[str, Any] = {
            "url_hash": b.url_hash,
            "content_sha256": b.content_sha256,
            "size": b.size,
            "updated_at": b.mtime,
        }

        send_inline = not b.is_chunked()

        if not send_inline and chunked_supported:
            blob_url = f"{base_url}/api/v1/blobs/{b.content_sha256}"
            try:
                head_status = _http_head_status(blob_url, token)
            except Exception as e:
                result.errors.append(f"conversations: HEAD {b.content_sha256[:12]}: {e}")
                continue

            if head_status == 200:
                pass  # Blob already on server; pointer is enough.
            else:
                try:
                    with open(b.local_path, "rb") as f:
                        raw = f.read()
                    _http_post_bytes(
                        f"{base_url}/api/v1/blobs",
                        raw,
                        token,
                    )
                except urllib.error.HTTPError as e:
                    if e.code in (404, 405):
                        # Server doesn't support blob endpoint — fall back
                        # to inline for this and all subsequent blobs.
                        chunked_supported = False
                        send_inline = True
                    else:
                        result.errors.append(
                            f"conversations: POST blob {b.content_sha256[:12]}: {e}"
                        )
                        continue
                except OSError as e:
                    result.errors.append(
                        f"conversations: read {b.local_path}: {e}"
                    )
                    continue
                except Exception as e:
                    result.errors.append(
                        f"conversations: POST blob {b.content_sha256[:12]}: {e}"
                    )
                    continue
        elif not send_inline and not chunked_supported:
            send_inline = True

        if send_inline:
            try:
                with open(b.local_path, "rb") as f:
                    raw = f.read()
            except OSError as e:
                result.errors.append(f"conversations: read {b.local_path}: {e}")
                continue
            try:
                item["content"] = raw.decode("utf-8")
            except UnicodeDecodeError:
                import base64
                item["content_b64"] = base64.b64encode(raw).decode("ascii")

        items.append(item)
        pushed_mtimes.append(b.mtime)

    if not items:
        return

    try:
        _http_post(
            f"{base_url}/api/v1/sync/conversations",
            {"project_id": project_id, "items": items},
            token,
        )
        result.conversations_pushed = len(items)
        max_mtime = max(pushed_mtimes, default=None)
        if max_mtime:
            last_push["conversations_max_updated_at"] = max_mtime
    except Exception as e:
        result.errors.append(f"conversations: {e}")


# -------------------------------------------------------------------
# Pull
# -------------------------------------------------------------------

@dataclass
class PullResult:
    traces_pulled: int = 0
    ledgers_pulled: int = 0
    commit_links_pulled: int = 0
    conversations_pulled: int = 0
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False


def _append_jsonl_dedupe(path: Path, records: list[dict], key: str = "id") -> int:
    """Append records to a JSONL file, skipping existing keys."""
    existing_keys: set[str] = set()
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    k = json.loads(line).get(key, "")
                    if k:
                        existing_keys.add(k)
                except json.JSONDecodeError:
                    continue

    added = 0
    with open(path, "a") as f:
        for rec in records:
            k = rec.get(key, "")
            if k and k in existing_keys:
                continue
            f.write(json.dumps(rec) + "\n")
            added += 1
            if k:
                existing_keys.add(k)
    return added


def pull(
    project_id: str,
    remote_name: str | None = None,
    *,
    since: str | None = None,
    dry_run: bool = False,
) -> PullResult:
    """Pull remote data into local storage."""
    result = PullResult(dry_run=dry_run)

    rname, rconf = resolve_remote(project_id, remote_name)
    base_url = get_remote_url(rconf).rstrip("/")
    token = get_remote_token(rconf)

    if not base_url:
        result.errors.append("Remote has no URL configured.")
        return result

    sync_state = _load_sync_state(project_id)
    remote_state = sync_state.setdefault("remotes", {}).setdefault(rname, {})
    last_pull_at = remote_state.get("last_pull", {}).get("at")

    effective_since = since or last_pull_at or ""

    ensure_project_dir(project_id)

    # --- Traces ---
    try:
        params = {"project_id": project_id, "limit": "500"}
        if effective_since:
            params["since"] = effective_since
        qs = urllib.parse.urlencode(params)
        data = _http_get(f"{base_url}/api/v1/sync/traces?{qs}", token)
        items = data.get("items", data.get("traces", []))
        if items and not dry_run:
            result.traces_pulled = _append_jsonl_dedupe(
                get_traces_path(project_id), items, key="id",
            )
        elif items:
            result.traces_pulled = len(items)
    except Exception as e:
        result.errors.append(f"traces: {e}")

    # --- Ledgers ---
    try:
        params = {"project_id": project_id, "limit": "500"}
        if effective_since:
            params["since"] = effective_since
        qs = urllib.parse.urlencode(params)
        data = _http_get(f"{base_url}/api/v1/sync/ledgers?{qs}", token)
        items = data.get("items", [])
        if items and not dry_run:
            result.ledgers_pulled = _append_jsonl_dedupe(
                get_ledgers_path(project_id), items, key="commit_sha",
            )
        elif items:
            result.ledgers_pulled = len(items)
    except Exception as e:
        result.errors.append(f"ledgers: {e}")

    # --- Commit links ---
    try:
        params = {"project_id": project_id, "limit": "500"}
        if effective_since:
            params["since"] = effective_since
        qs = urllib.parse.urlencode(params)
        data = _http_get(f"{base_url}/api/v1/sync/commit-links?{qs}", token)
        items = data.get("items", [])
        if items and not dry_run:
            result.commit_links_pulled = _append_jsonl_dedupe(
                get_commit_links_path(project_id), items, key="commit_sha",
            )
        elif items:
            result.commit_links_pulled = len(items)
    except Exception as e:
        result.errors.append(f"commit-links: {e}")

    # --- Conversations ---
    try:
        params = {"project_id": project_id, "limit": "500"}
        if effective_since:
            params["since"] = effective_since
        qs = urllib.parse.urlencode(params)
        data = _http_get(f"{base_url}/api/v1/sync/conversations?{qs}", token)
        items = data.get("items", [])
        if items and not dry_run:
            result.conversations_pulled = _materialize_conversations(
                project_id=project_id,
                base_url=base_url,
                token=token,
                items=items,
                errors=result.errors,
            )
        elif items:
            result.conversations_pulled = len(items)
    except urllib.error.HTTPError as e:
        # Older services without the conversations endpoint return 404 —
        # silently skip to keep pull working against legacy servers.
        if e.code != 404:
            result.errors.append(f"conversations: {e}")
    except Exception as e:
        result.errors.append(f"conversations: {e}")

    # Update pull cursor
    if not dry_run:
        now = datetime.now(timezone.utc).isoformat()
        remote_state["last_pull"] = {"at": now}
        _save_sync_state(project_id, sync_state)

    return result


def _materialize_conversations(
    *,
    project_id: str,
    base_url: str,
    token: str | None,
    items: list[dict[str, Any]],
    errors: list[str],
) -> int:
    """Write each pulled conversation blob into the local content-addressed
    cache. Inline ``content`` is used when present; otherwise the blob is
    fetched from ``GET /api/v1/blobs/<sha>``. Skips blobs already cached.

    Returns the count of blobs newly written.
    """
    written = 0
    for item in items:
        sha = item.get("content_sha256")
        if not isinstance(sha, str) or not sha:
            continue
        cache_path = cache_path_for_sha(project_id, sha)
        if cache_path.is_file():
            continue

        raw: bytes | None = None
        if isinstance(item.get("content"), str):
            raw = item["content"].encode("utf-8")
        elif isinstance(item.get("content_b64"), str):
            import base64
            try:
                raw = base64.b64decode(item["content_b64"])
            except Exception as e:
                errors.append(f"conversations: bad b64 for {sha[:12]}: {e}")
                continue
        else:
            try:
                raw = _http_get_bytes(f"{base_url}/api/v1/blobs/{sha}", token)
            except Exception as e:
                errors.append(f"conversations: GET blob {sha[:12]}: {e}")
                continue

        try:
            write_blob_to_cache(project_id, sha, raw)
            written += 1
        except ValueError as e:
            errors.append(f"conversations: {e}")
        except OSError as e:
            errors.append(f"conversations: write {sha[:12]}: {e}")
    return written


# -------------------------------------------------------------------
# Status
# -------------------------------------------------------------------

@dataclass
class StatusReport:
    project_id: str
    remote_name: str | None = None
    remote_url: str | None = None
    total_traces: int = 0
    total_ledgers: int = 0
    total_commit_links: int = 0
    unpushed_traces: int = 0
    unpushed_ledgers: int = 0
    unpushed_commit_links: int = 0
    unattributed_traces: int = 0
    last_push: str | None = None
    last_pull: str | None = None


def status(project_id: str, remote_name: str | None = None) -> StatusReport:
    """Compute a git-status-like report of local vs remote sync."""
    report = StatusReport(project_id=project_id)

    traces = _read_traces(project_id)
    ledgers = _read_ledgers(project_id)
    links = _read_commit_links(project_id)
    report.total_traces = len(traces)
    report.total_ledgers = len(ledgers)
    report.total_commit_links = len(links)

    attributed_ids = compute_attributed_trace_ids(project_id)
    report.unattributed_traces = sum(
        1 for t in traces if t.get("id", "") not in attributed_ids
    )

    try:
        rname, rconf = resolve_remote(project_id, remote_name)
        report.remote_name = rname
        report.remote_url = get_remote_url(rconf)
    except ValueError:
        return report

    sync_state = _load_sync_state(project_id)
    remote_state = sync_state.get("remotes", {}).get(rname, {})
    last_push = remote_state.get("last_push", {})
    last_pull = remote_state.get("last_pull", {})

    push_ts = last_push.get("traces_max_timestamp")
    pull_at = last_pull.get("at")
    report.last_push = push_ts
    report.last_pull = pull_at

    report.unpushed_traces = sum(
        1 for t in traces
        if t.get("id", "") in attributed_ids
        and (not push_ts or t.get("timestamp", "") > push_ts)
    )
    ledger_ts = last_push.get("ledgers_max_commit_at")
    report.unpushed_ledgers = sum(
        1 for l in ledgers
        if not ledger_ts or (l.get("committed_at") or l.get("created_at", "")) > ledger_ts
    )
    link_ts = last_push.get("commit_links_max_commit_at")
    report.unpushed_commit_links = sum(
        1 for c in links
        if not link_ts or (c.get("committed_at") or c.get("created_at", "")) > link_ts
    )

    return report
