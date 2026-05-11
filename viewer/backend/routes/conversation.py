"""
/api/conversation — fetch full conversation content by ``conversation_id``.

GET ?conversation_id=<64-hex>
Behavior:
  - Resolves the latest ``content_sha256`` referenced by traces for the given
    ``conversation_id`` and returns the cached transcript bytes verbatim from
    ``<project>/conversations/<sha[:2]>/<sha>``.

Backwards-compat: ``?url=<conversation_id>`` is accepted for the moment so the
front-end transition can be staged in a single PR.
"""
from __future__ import annotations

import os
from urllib.parse import unquote


def _resolve_project_id_for_root(project_root: str) -> str | None:
    try:
        from agent_trace.storage import resolve_project_id
        return resolve_project_id(project_root, create=False)
    except Exception:
        return None


def _latest_content_sha_for_conversation(pid: str, conversation_id: str) -> str | None:
    """Latest cached content_sha256 for a given conversation_id."""
    from agent_trace.conversations import latest_sha_for_conversation
    return latest_sha_for_conversation(pid, conversation_id)


def get_conversation_content(
    project_root: str, conversation_id: str,
) -> tuple[dict | None, str | None, int]:
    """Resolve cached transcript bytes for ``conversation_id``.

    Returns ``(result_dict, error, status)`` matching the rest of the viewer API.
    """
    if not conversation_id or not isinstance(conversation_id, str):
        return None, "conversation_id required", 400
    conversation_id = unquote(conversation_id.strip())
    if not conversation_id:
        return None, "conversation_id required", 400

    pid = _resolve_project_id_for_root(project_root)
    if not pid:
        return None, "Project has no agent-trace data", 404

    sha = _latest_content_sha_for_conversation(pid, conversation_id)
    if not sha:
        return None, "Conversation not found", 404

    from agent_trace.conversations import cache_path_for_sha

    p = cache_path_for_sha(pid, sha)
    if not p.is_file():
        return None, "Conversation transcript not in local cache", 404
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None, "Could not read cached transcript", 404
    return {"content": content}, None, 200
