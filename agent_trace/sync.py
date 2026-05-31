"""
Push/Pull sync protocol — content-ID manifests, no timestamp cursors as truth.

Each remote keeps a per-resource set of IDs known to be on the server in
``sync-state.json``. Push sends only locally-held items whose ID is not yet
in the manifest; pull paginates through the server, dedupes by ID against
the local store, and adds every received ID to the manifest. Status is
``local_ids - synced_ids`` — no timestamp comparisons, no false positives
after pull.

The per-resource ``cursor`` (server's ``max(created_at)`` from the prior
response) is a paging hint only. The manifest is the source of truth, so
losing or rebuilding the cursor only costs one extra full scan; it never
drops data.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .conversations import (
    ConversationBlob,
    cache_path_for_sha,
    enumerate_local_blobs,
    update_conversation_index,
    write_blob_to_cache,
)
from .remote import (
    TokenScopeError,
    assert_token_matches_url,
    get_remote_base_url,
    get_remote_org_slug,
    get_remote_project_slug,
    get_remote_token,
    get_remote_url,
    resolve_remote,
)
from .storage import (
    ensure_project_dir,
    get_commit_links_path,
    get_ledgers_path,
    get_project_dir,
    get_traces_path,
)


SYNC_STATE_VERSION = 2

# Server-side page size for pull. The server caps at 1000 (app.py).
PULL_PAGE_LIMIT = 500

# Per-batch size for push payloads. Keeps individual POSTs bounded so a
# single oversized JSON body can't stall a sync.
PUSH_BATCH_SIZE = 500


# -------------------------------------------------------------------
# Sync state persistence
# -------------------------------------------------------------------

def _sync_state_path(project_id: str) -> Path:
    return get_project_dir(project_id) / "sync-state.json"


def _empty_remote_state() -> dict[str, Any]:
    return {
        "synced": {
            "trace_ids": [],
            "ledger_shas": [],
            "commit_link_shas": [],
            "blob_shas": [],
            "conversation_ids": [],
            "summary_keys": [],
        },
        "cursor": {
            "traces": None,
            "ledgers": None,
            "commit_links": None,
            "conversations": None,
            "summaries": None,
        },
    }


def _load_sync_state(project_id: str) -> dict[str, Any]:
    """Load sync-state.json, normalising legacy or partial files in memory.

    Legacy files (v1) used ``last_push`` / ``last_pull`` timestamp cursors.
    Those are silently discarded — the manifest will rebuild itself on the
    next sync (server dedupes pushes; pulls dedupe by id locally).
    """
    p = _sync_state_path(project_id)
    if not p.is_file():
        return {"version": SYNC_STATE_VERSION, "remotes": {}}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": SYNC_STATE_VERSION, "remotes": {}}
    if not isinstance(data, dict):
        return {"version": SYNC_STATE_VERSION, "remotes": {}}

    out: dict[str, Any] = {"version": SYNC_STATE_VERSION, "remotes": {}}
    raw_remotes = data.get("remotes")
    if isinstance(raw_remotes, dict):
        for name, rs in raw_remotes.items():
            if not isinstance(rs, dict):
                continue
            out["remotes"][str(name)] = _normalise_remote_state(rs)
    return out


def _normalise_remote_state(rs: dict[str, Any]) -> dict[str, Any]:
    base = _empty_remote_state()
    synced = rs.get("synced")
    if isinstance(synced, dict):
        for k in base["synced"]:
            v = synced.get(k)
            if isinstance(v, list):
                base["synced"][k] = [str(x) for x in v if isinstance(x, str)]
    cursor = rs.get("cursor")
    if isinstance(cursor, dict):
        for k in base["cursor"]:
            v = cursor.get(k)
            if isinstance(v, str):
                base["cursor"][k] = v
    return base


def _save_sync_state(project_id: str, state: dict[str, Any]) -> None:
    ensure_project_dir(project_id)
    p = _sync_state_path(project_id)
    p.write_text(json.dumps(state, indent=2) + "\n")


def _get_remote_state(state: dict[str, Any], rname: str) -> dict[str, Any]:
    remotes = state.setdefault("remotes", {})
    rs = remotes.get(rname)
    if not isinstance(rs, dict):
        rs = _empty_remote_state()
        remotes[rname] = rs
    return rs


def _synced_set(rs: dict[str, Any], key: str) -> set[str]:
    return set(rs["synced"].get(key, []))


def _persist_synced_set(rs: dict[str, Any], key: str, ids: set[str]) -> None:
    rs["synced"][key] = sorted(ids)


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


def _chunked(items: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


# -------------------------------------------------------------------
# Push
# -------------------------------------------------------------------

@dataclass
class PushResult:
    traces_pushed: int = 0
    ledgers_pushed: int = 0
    commit_links_pushed: int = 0
    conversations_pushed: int = 0
    summaries_pushed: int = 0
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
    """Push local items the server hasn't seen yet.

    ``full=True`` includes unattributed traces (those referenced by no local
    ledger). ``only`` restricts to a single resource class. ``since`` is an
    ISO timestamp lower-bound on the item's own timestamp field — useful for
    one-off backfills, but unrelated to the synced manifest.
    """
    result = PushResult(dry_run=dry_run)

    rname, rconf = resolve_remote(project_id, remote_name)
    base_url = get_remote_base_url(rconf).rstrip("/")
    wire_project_id = get_remote_project_slug(rconf)
    wire_org_slug = get_remote_org_slug(rconf)
    token = get_remote_token(rconf)

    if not base_url:
        result.errors.append("Remote has no URL configured.")
        return result
    if not wire_project_id:
        result.errors.append(
            "Remote URL is missing the project path. "
            "Expected ``<scheme>://<host>/<org>/<project>`` or ``<scheme>://<host>/at/<org>/<project>`` (gateway path). "
            "Run `agent-trace remote set-url` to fix it."
        )
        return result

    # Pre-flight scope check. The server already gates on the token's org,
    # so a mismatched URL would silently push to the *token's* org rather
    # than the org the user typed. We catch that here so the failure mode
    # is loud and explicit instead of "data went somewhere else".
    if token and wire_org_slug:
        try:
            assert_token_matches_url(
                base_url, token,
                expected_org_slug=wire_org_slug,
                expected_project_slug=wire_project_id,
            )
        except TokenScopeError as e:
            result.errors.append(f"scope check ({e.code}): {e}")
            return result

    attributed_ids = compute_attributed_trace_ids(project_id) if not full else None

    sync_state = _load_sync_state(project_id)
    rs = _get_remote_state(sync_state, rname)

    # --- Traces ---
    if only is None or only == "traces":
        synced_ids = _synced_set(rs, "trace_ids")
        traces = _read_traces(project_id)
        held_back = 0
        to_push: list[dict[str, Any]] = []
        for t in traces:
            tid = t.get("id", "")
            if not tid:
                continue
            if tid in synced_ids:
                continue
            if since and t.get("timestamp", "") < since:
                continue
            if attributed_ids is not None and tid not in attributed_ids:
                held_back += 1
                continue
            to_push.append(t)

        result.traces_held_back = held_back

        if to_push and not dry_run:
            pushed_ids = _push_batched(
                f"{base_url}/api/v1/sync/traces",
                wire_project_id,
                to_push,
                token,
                id_of=lambda t: t.get("id", ""),
                error_label="traces",
                errors=result.errors,
            )
            synced_ids.update(pushed_ids)
            _persist_synced_set(rs, "trace_ids", synced_ids)
            result.traces_pushed = len(pushed_ids)
        elif to_push:
            result.traces_pushed = len(to_push)

    # --- Ledgers ---
    if only is None or only == "ledgers":
        synced_shas = _synced_set(rs, "ledger_shas")
        ledgers = _read_ledgers(project_id)
        to_push_l: list[dict[str, Any]] = []
        for led in ledgers:
            sha = led.get("commit_sha", "")
            if not sha or sha in synced_shas:
                continue
            if since:
                ca = led.get("committed_at") or led.get("created_at", "")
                if ca and ca < since:
                    continue
            to_push_l.append(led)

        if to_push_l and not dry_run:
            pushed_shas = _push_batched(
                f"{base_url}/api/v1/sync/ledgers",
                wire_project_id,
                to_push_l,
                token,
                id_of=lambda l: l.get("commit_sha", ""),
                error_label="ledgers",
                errors=result.errors,
            )
            synced_shas.update(pushed_shas)
            _persist_synced_set(rs, "ledger_shas", synced_shas)
            result.ledgers_pushed = len(pushed_shas)
        elif to_push_l:
            result.ledgers_pushed = len(to_push_l)

    # --- Commit links ---
    if only is None or only == "commit-links":
        synced_shas = _synced_set(rs, "commit_link_shas")
        links = _read_commit_links(project_id)
        to_push_c: list[dict[str, Any]] = []
        for cl in links:
            sha = cl.get("commit_sha", "")
            if not sha or sha in synced_shas:
                continue
            if since:
                ca = cl.get("committed_at") or cl.get("created_at", "")
                if ca and ca < since:
                    continue
            to_push_c.append(cl)

        if to_push_c and not dry_run:
            pushed_shas = _push_batched(
                f"{base_url}/api/v1/sync/commit-links",
                wire_project_id,
                to_push_c,
                token,
                id_of=lambda c: c.get("commit_sha", ""),
                error_label="commit-links",
                errors=result.errors,
            )
            synced_shas.update(pushed_shas)
            _persist_synced_set(rs, "commit_link_shas", synced_shas)
            result.commit_links_pushed = len(pushed_shas)
        elif to_push_c:
            result.commit_links_pushed = len(to_push_c)

    # --- Conversations (chunked, hash-addressed) ---
    if only is None or only == "conversations":
        _push_conversations(
            project_id=project_id,
            wire_project_id=wire_project_id,
            base_url=base_url,
            token=token,
            rs=rs,
            since=since,
            dry_run=dry_run,
            result=result,
        )

    # --- Summaries ---
    if only is None or only == "summaries":
        _push_summaries(
            project_id=project_id,
            wire_project_id=wire_project_id,
            base_url=base_url,
            token=token,
            rs=rs,
            since=since,
            dry_run=dry_run,
            result=result,
        )

    if not dry_run:
        _save_sync_state(project_id, sync_state)

    return result


def _push_batched(
    url: str,
    wire_project_id: str,
    items: list[dict[str, Any]],
    token: str | None,
    *,
    id_of,
    error_label: str,
    errors: list[str],
) -> set[str]:
    """POST items in PUSH_BATCH_SIZE chunks. Returns set of IDs whose batch
    was acknowledged by the server. A failed batch leaves its IDs unsynced
    so the next push retries them."""
    acked: set[str] = set()
    for batch in _chunked(items, PUSH_BATCH_SIZE):
        try:
            _http_post(url, {"project_id": wire_project_id, "items": batch}, token)
        except Exception as e:
            errors.append(f"{error_label}: {e}")
            continue
        for item in batch:
            iid = id_of(item)
            if iid:
                acked.add(iid)
    return acked


def _push_conversations(
    *,
    project_id: str,
    wire_project_id: str,
    base_url: str,
    token: str | None,
    rs: dict[str, Any],
    since: str | None,
    dry_run: bool,
    result: PushResult,
) -> None:
    """Push transcript blobs and pointers, deduping by SHA-256.

    A conversation is "synced" when both its blob (sha) and its pointer
    (conversation_id) are confirmed on the server. We track them separately:

      - ``synced.blob_shas`` — bytes are present on the server.
      - ``synced.conversation_ids`` — pointer row exists on the server.

    Inline (size <= chunk threshold) conversations carry their bytes in the
    pointer POST, so a successful pointer push implies the blob is present
    too. Chunked conversations upload the blob first (HEAD/POST blob), then
    send a pointer-only POST.
    """
    blobs = enumerate_local_blobs(project_id)
    synced_blob_shas = _synced_set(rs, "blob_shas")
    synced_conv_ids = _synced_set(rs, "conversation_ids")

    pending: list[ConversationBlob] = []
    for b in blobs:
        if b.conversation_id in synced_conv_ids and b.content_sha256 in synced_blob_shas:
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
    item_conv_ids: list[str] = []
    item_blob_shas: list[str] = []
    chunked_supported = True

    for b in pending:
        cache_path = cache_path_for_sha(project_id, b.content_sha256)
        item: dict[str, Any] = {
            "conversation_id": b.conversation_id,
            "content_sha256": b.content_sha256,
            "size": b.size,
            "updated_at": b.mtime,
        }

        send_inline = not b.is_chunked()

        if not send_inline and chunked_supported:
            if b.content_sha256 in synced_blob_shas:
                pass  # Already-synced blob — pointer-only.
            else:
                blob_url = f"{base_url}/api/v1/blobs/{b.content_sha256}"
                try:
                    head_status = _http_head_status(blob_url, token)
                except Exception as e:
                    result.errors.append(f"conversations: HEAD {b.content_sha256[:12]}: {e}")
                    continue

                if head_status == 200:
                    synced_blob_shas.add(b.content_sha256)
                else:
                    try:
                        with open(cache_path, "rb") as f:
                            raw = f.read()
                        _http_post_bytes(f"{base_url}/api/v1/blobs", raw, token)
                        synced_blob_shas.add(b.content_sha256)
                    except urllib.error.HTTPError as e:
                        if e.code in (404, 405):
                            chunked_supported = False
                            send_inline = True
                        else:
                            result.errors.append(
                                f"conversations: POST blob {b.content_sha256[:12]}: {e}"
                            )
                            continue
                    except OSError as e:
                        result.errors.append(f"conversations: read cache {b.content_sha256[:12]}: {e}")
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
                with open(cache_path, "rb") as f:
                    raw = f.read()
            except OSError as e:
                result.errors.append(f"conversations: read cache {b.content_sha256[:12]}: {e}")
                continue
            try:
                item["content"] = raw.decode("utf-8")
            except UnicodeDecodeError:
                import base64
                item["content_b64"] = base64.b64encode(raw).decode("ascii")

        items.append(item)
        item_conv_ids.append(b.conversation_id)
        item_blob_shas.append(b.content_sha256)

    if not items:
        _persist_synced_set(rs, "blob_shas", synced_blob_shas)
        return

    pushed_count = 0
    for start in range(0, len(items), PUSH_BATCH_SIZE):
        batch = items[start : start + PUSH_BATCH_SIZE]
        batch_conv_ids = item_conv_ids[start : start + PUSH_BATCH_SIZE]
        batch_blob_shas = item_blob_shas[start : start + PUSH_BATCH_SIZE]
        try:
            _http_post(
                f"{base_url}/api/v1/sync/conversations",
                {"project_id": wire_project_id, "items": batch},
                token,
            )
        except Exception as e:
            result.errors.append(f"conversations: {e}")
            continue
        synced_conv_ids.update(batch_conv_ids)
        synced_blob_shas.update(batch_blob_shas)
        pushed_count += len(batch)

    _persist_synced_set(rs, "blob_shas", synced_blob_shas)
    _persist_synced_set(rs, "conversation_ids", synced_conv_ids)
    result.conversations_pushed = pushed_count


def _summary_row_key(row: dict[str, Any]) -> str:
    cid = str(row.get("conversation_id") or "")
    ts = str(row.get("created_at") or "")
    return f"{cid}:{ts}"


def _push_summaries(
    *,
    project_id: str,
    wire_project_id: str,
    base_url: str,
    token: str | None,
    rs: dict[str, Any],
    since: str | None,
    dry_run: bool,
    result: PushResult,
) -> None:
    """Push summary rows (``session-summaries.jsonl``) the server hasn't seen yet."""
    from .summary import iter_summary_rows

    rows = iter_summary_rows(project_id)
    if not rows:
        return

    synced_keys = _synced_set(rs, "summary_keys")
    pending: list[dict[str, Any]] = []
    for row in rows:
        cid = str(row.get("conversation_id") or "")
        ts = str(row.get("created_at") or "")
        if not cid or not ts:
            continue
        key = f"{cid}:{ts}"
        if key in synced_keys:
            continue
        if since and ts < since:
            continue
        pending.append(row)

    if not pending:
        return

    if dry_run:
        result.summaries_pushed = len(pending)
        return

    pushed_count = 0
    for start in range(0, len(pending), PUSH_BATCH_SIZE):
        batch = pending[start : start + PUSH_BATCH_SIZE]
        try:
            _http_post(
                f"{base_url}/api/v1/sync/summaries",
                {"project_id": wire_project_id, "items": batch},
                token,
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Older service without the summaries endpoint. Stop the leg
                # cleanly so push doesn't bail on otherwise-good batches.
                result.errors.append("summaries: endpoint not implemented (server too old)")
                break
            result.errors.append(f"summaries: {e}")
            continue
        except Exception as e:
            result.errors.append(f"summaries: {e}")
            continue
        for row in batch:
            synced_keys.add(_summary_row_key(row))
        pushed_count += len(batch)

    _persist_synced_set(rs, "summary_keys", synced_keys)
    result.summaries_pushed = pushed_count


# -------------------------------------------------------------------
# Pull
# -------------------------------------------------------------------

@dataclass
class PullResult:
    traces_pulled: int = 0
    ledgers_pulled: int = 0
    commit_links_pulled: int = 0
    conversations_pulled: int = 0
    summaries_pulled: int = 0
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


def _pull_paginated(
    *,
    base_url: str,
    path: str,
    wire_project_id: str,
    token: str | None,
    cursor: str | None,
    error_label: str,
    errors: list[str],
) -> tuple[list[dict[str, Any]], str | None]:
    """Walk pages of ``GET <path>?since=<cursor>`` until a short page lands.

    Returns ``(all_items, new_cursor)``. ``new_cursor`` is the last
    ``max_timestamp`` returned by the server (or the original cursor if no
    items came back).
    """
    all_items: list[dict[str, Any]] = []
    next_cursor = cursor
    while True:
        params: dict[str, str] = {"project_id": wire_project_id, "limit": str(PULL_PAGE_LIMIT)}
        if next_cursor:
            params["since"] = next_cursor
        qs = urllib.parse.urlencode(params)
        try:
            data = _http_get(f"{base_url}{path}?{qs}", token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Endpoint not implemented (e.g., legacy server, conversations).
                raise
            errors.append(f"{error_label}: {e}")
            break
        except Exception as e:
            errors.append(f"{error_label}: {e}")
            break

        items = data.get("items") or data.get("traces") or []
        max_ts = data.get("max_timestamp")
        if not items:
            break

        all_items.extend(items)
        if isinstance(max_ts, str) and max_ts:
            next_cursor = max_ts
        if len(items) < PULL_PAGE_LIMIT:
            break
        if not isinstance(max_ts, str) or not max_ts:
            # Server didn't advance the cursor — stop to avoid infinite loop.
            break
    return all_items, next_cursor


def pull(
    project_id: str,
    remote_name: str | None = None,
    *,
    since: str | None = None,
    dry_run: bool = False,
) -> PullResult:
    """Pull remote data into local storage.

    Paginates each resource until the server returns a short page, advances
    the per-resource cursor to the server-returned ``max_timestamp``, and
    adds every received ID to the synced manifest so subsequent status /
    push calls don't re-treat them as local-only.

    ``since`` overrides the stored cursor for this run only — useful for
    backfilling after manifest loss.
    """
    result = PullResult(dry_run=dry_run)

    rname, rconf = resolve_remote(project_id, remote_name)
    base_url = get_remote_base_url(rconf).rstrip("/")
    wire_project_id = get_remote_project_slug(rconf)
    wire_org_slug = get_remote_org_slug(rconf)
    token = get_remote_token(rconf)

    if not base_url:
        result.errors.append("Remote has no URL configured.")
        return result
    if not wire_project_id:
        result.errors.append(
            "Remote URL is missing the project path (<scheme>://<host>/<org>/<project>)."
        )
        return result

    # Same pre-flight as ``push``: refuse to pull if the bound URL's org
    # disagrees with the token's actual org. Avoids "I pulled from /foo-org
    # but rows came from /bar-org" surprises.
    if token and wire_org_slug:
        try:
            assert_token_matches_url(
                base_url, token,
                expected_org_slug=wire_org_slug,
                expected_project_slug=wire_project_id,
            )
        except TokenScopeError as e:
            result.errors.append(f"scope check ({e.code}): {e}")
            return result

    sync_state = _load_sync_state(project_id)
    rs = _get_remote_state(sync_state, rname)

    ensure_project_dir(project_id)

    def cursor_for(name: str) -> str | None:
        return since if since else rs["cursor"].get(name)

    # --- Traces ---
    try:
        items, new_cursor = _pull_paginated(
            base_url=base_url,
            path="/api/v1/sync/traces",
            wire_project_id=wire_project_id,
            token=token,
            cursor=cursor_for("traces"),
            error_label="traces",
            errors=result.errors,
        )
        if items and not dry_run:
            result.traces_pulled = _append_jsonl_dedupe(
                get_traces_path(project_id), items, key="id",
            )
            ids = {t.get("id", "") for t in items if t.get("id")}
            synced = _synced_set(rs, "trace_ids")
            synced.update(ids)
            _persist_synced_set(rs, "trace_ids", synced)
            if new_cursor:
                rs["cursor"]["traces"] = new_cursor
        elif items:
            result.traces_pulled = len(items)
    except Exception as e:
        result.errors.append(f"traces: {e}")

    # --- Ledgers ---
    try:
        items, new_cursor = _pull_paginated(
            base_url=base_url,
            path="/api/v1/sync/ledgers",
            wire_project_id=wire_project_id,
            token=token,
            cursor=cursor_for("ledgers"),
            error_label="ledgers",
            errors=result.errors,
        )
        if items and not dry_run:
            result.ledgers_pulled = _append_jsonl_dedupe(
                get_ledgers_path(project_id), items, key="commit_sha",
            )
            shas = {l.get("commit_sha", "") for l in items if l.get("commit_sha")}
            synced = _synced_set(rs, "ledger_shas")
            synced.update(shas)
            _persist_synced_set(rs, "ledger_shas", synced)
            if new_cursor:
                rs["cursor"]["ledgers"] = new_cursor
        elif items:
            result.ledgers_pulled = len(items)
    except Exception as e:
        result.errors.append(f"ledgers: {e}")

    # --- Commit links ---
    try:
        items, new_cursor = _pull_paginated(
            base_url=base_url,
            path="/api/v1/sync/commit-links",
            wire_project_id=wire_project_id,
            token=token,
            cursor=cursor_for("commit_links"),
            error_label="commit-links",
            errors=result.errors,
        )
        if items and not dry_run:
            result.commit_links_pulled = _append_jsonl_dedupe(
                get_commit_links_path(project_id), items, key="commit_sha",
            )
            shas = {c.get("commit_sha", "") for c in items if c.get("commit_sha")}
            synced = _synced_set(rs, "commit_link_shas")
            synced.update(shas)
            _persist_synced_set(rs, "commit_link_shas", synced)
            if new_cursor:
                rs["cursor"]["commit_links"] = new_cursor
        elif items:
            result.commit_links_pulled = len(items)
    except Exception as e:
        result.errors.append(f"commit-links: {e}")

    # --- Conversations ---
    try:
        items, new_cursor = _pull_paginated(
            base_url=base_url,
            path="/api/v1/sync/conversations",
            wire_project_id=wire_project_id,
            token=token,
            cursor=cursor_for("conversations"),
            error_label="conversations",
            errors=result.errors,
        )
        if items and not dry_run:
            result.conversations_pulled = _materialize_conversations(
                project_id=project_id,
                base_url=base_url,
                token=token,
                items=items,
                rs=rs,
                errors=result.errors,
            )
            if new_cursor:
                rs["cursor"]["conversations"] = new_cursor
        elif items:
            result.conversations_pulled = len(items)
    except urllib.error.HTTPError as e:
        # Older services without the conversations endpoint return 404.
        if e.code != 404:
            result.errors.append(f"conversations: {e}")
    except Exception as e:
        result.errors.append(f"conversations: {e}")

    # --- Summaries ---
    try:
        items, new_cursor = _pull_paginated(
            base_url=base_url,
            path="/api/v1/sync/summaries",
            wire_project_id=wire_project_id,
            token=token,
            cursor=cursor_for("summaries"),
            error_label="summaries",
            errors=result.errors,
        )
        if items and not dry_run:
            result.summaries_pulled = _materialize_summaries(
                project_id=project_id,
                items=items,
                rs=rs,
                errors=result.errors,
            )
            if new_cursor:
                rs["cursor"]["summaries"] = new_cursor
        elif items:
            result.summaries_pulled = len(items)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            result.errors.append(f"summaries: {e}")
    except Exception as e:
        result.errors.append(f"summaries: {e}")

    if not dry_run:
        _save_sync_state(project_id, sync_state)

    return result


def _materialize_conversations(
    *,
    project_id: str,
    base_url: str,
    token: str | None,
    items: list[dict[str, Any]],
    rs: dict[str, Any],
    errors: list[str],
) -> int:
    """Write each pulled conversation blob into the local content-addressed
    cache and add its sha + conversation_id to the synced manifest. Inline
    content is used when present; chunked blobs are fetched from
    ``GET /api/v1/blobs/<sha>``. Returns count of blobs newly written.
    """
    written = 0
    synced_blob_shas = _synced_set(rs, "blob_shas")
    synced_conv_ids = _synced_set(rs, "conversation_ids")

    for item in items:
        sha = item.get("content_sha256")
        if isinstance(sha, str) and sha:
            cache_path = cache_path_for_sha(project_id, sha)
            if not cache_path.is_file():
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
                    continue
                except OSError as e:
                    errors.append(f"conversations: write {sha[:12]}: {e}")
                    continue
            synced_blob_shas.add(sha)

        cid = item.get("conversation_id")
        if isinstance(cid, str) and cid:
            synced_conv_ids.add(cid)
            # Server delivers items in updated_at ASC order, so the last
            # pair we see for a given conversation_id is the freshest snapshot.
            if isinstance(sha, str) and sha:
                update_conversation_index(project_id, cid, sha)

    _persist_synced_set(rs, "blob_shas", synced_blob_shas)
    _persist_synced_set(rs, "conversation_ids", synced_conv_ids)
    return written


def _materialize_summaries(
    *,
    project_id: str,
    items: list[dict[str, Any]],
    rs: dict[str, Any],
    errors: list[str],
) -> int:
    """Append pulled summary rows to ``session-summaries.jsonl``.

    Dedup by synthetic ``<conversation_id>:<created_at>`` key — that pair is
    the natural unique identifier (the same conversation can have multiple
    regenerated summaries over time, each with its own timestamp).
    """
    from .summary import append_summary, iter_summary_rows

    synced_keys = _synced_set(rs, "summary_keys")
    existing_keys = {
        _summary_row_key(row) for row in iter_summary_rows(project_id)
    }
    written = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("conversation_id")
        summary = item.get("summary")
        if not isinstance(cid, str) or not isinstance(summary, str) or not cid or not summary:
            continue
        created_at = item.get("created_at") or item.get("updated_at")
        if not isinstance(created_at, str) or not created_at:
            errors.append(f"summaries: missing created_at for {cid[:12]}")
            continue
        key = f"{cid}:{created_at}"
        if key in existing_keys:
            synced_keys.add(key)
            continue
        session_id = item.get("session_id") if isinstance(item.get("session_id"), str) else None
        row = append_summary(
            project_id, cid, summary,
            session_id=session_id, created_at=created_at,
        )
        if row is not None:
            existing_keys.add(key)
            synced_keys.add(key)
            written += 1

    _persist_synced_set(rs, "summary_keys", synced_keys)
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
    traces_cursor: str | None = None
    ledgers_cursor: str | None = None
    commit_links_cursor: str | None = None
    conversations_cursor: str | None = None


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
    rs = _get_remote_state(sync_state, rname)

    synced_trace_ids = _synced_set(rs, "trace_ids")
    synced_ledger_shas = _synced_set(rs, "ledger_shas")
    synced_link_shas = _synced_set(rs, "commit_link_shas")

    report.unpushed_traces = sum(
        1 for t in traces
        if t.get("id", "") in attributed_ids
        and t.get("id", "") not in synced_trace_ids
    )
    report.unpushed_ledgers = sum(
        1 for l in ledgers
        if l.get("commit_sha", "") and l.get("commit_sha", "") not in synced_ledger_shas
    )
    report.unpushed_commit_links = sum(
        1 for c in links
        if c.get("commit_sha", "") and c.get("commit_sha", "") not in synced_link_shas
    )

    cursors = rs.get("cursor", {})
    report.traces_cursor = cursors.get("traces")
    report.ledgers_cursor = cursors.get("ledgers")
    report.commit_links_cursor = cursors.get("commit_links")
    report.conversations_cursor = cursors.get("conversations")

    return report
