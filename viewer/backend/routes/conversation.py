"""
/api/conversation — fetch full conversation content by URL or path.

GET ?url=...  or  ?path=... (path only in local mode)
Behavior:
  - If url is http:// or https://: return { "open_external": true, "url": "<url>" } so frontend opens in new tab.
  - If storage is "local": read from filesystem. Accept file://... (absolute or relative) or bare path.
    Absolute paths (e.g. file:///Users/.../.cursor/.../agent-transcripts/xxx.txt) are allowed if under
    project root or under the user's home directory. Relative paths are resolved against project root.
  - If storage is "remote": fetch from agent-trace-service GET /api/v1/conversations/content, return { "content": "..." }.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import unquote


def _load_project_config(project_root: str) -> dict | None:
    """Load .agent-trace/config.json. Returns None if not present or invalid."""
    path = os.path.join(os.path.abspath(project_root), ".agent-trace", "config.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _get_auth_token(config: dict | None) -> str | None:
    """Resolve auth token: AGENT_TRACE_TOKEN env, then global config, then project config."""
    token = os.environ.get("AGENT_TRACE_TOKEN")
    if token:
        return token
    global_path = os.path.expanduser("~/.agent-trace/config.json")
    if os.path.isfile(global_path):
        try:
            with open(global_path) as f:
                global_cfg = json.load(f)
                if global_cfg.get("auth_token"):
                    return global_cfg["auth_token"]
        except (OSError, json.JSONDecodeError):
            pass
    if config and config.get("auth_token"):
        return config["auth_token"]
    return None


def _get_service_url(config: dict | None) -> str:
    default = os.environ.get("AGENT_TRACE_URL", "http://localhost:5000").rstrip("/")
    if config and config.get("service_url"):
        return config["service_url"].rstrip("/")
    return default


def get_conversation_content(project_root: str, url: str) -> tuple[dict | None, str | None, int]:
    """
    Resolve conversation content or action based on URL and project config.

    Returns (result_dict, error_message, status_code).
    - ({ "content": "..." }, None, 200) — show content in modal
    - ({ "open_external": true, "url": "..." }, None, 200) — open URL in new tab
    - (None, message, 400/403/404/502) on failure
    """
    if not url or not isinstance(url, str):
        return None, "url required", 400
    url = unquote(url.strip())
    if not url:
        return None, "url required", 400

    # External URL (e.g. Cursor site) — tell frontend to open in new tab
    if url.startswith("https://") or url.startswith("http://"):
        return {"open_external": True, "url": url}, None, 200

    config = _load_project_config(project_root) or {}
    storage = config.get("storage", "local")

    # Local mode: read from filesystem. Accept file:// URL (absolute or relative) or bare path.
    # Absolute paths (e.g. file:///Users/.../.cursor/.../agent-transcripts/xxx.txt) are allowed
    # if under project root or under the user's home directory (Cursor stores transcripts there).
    if storage == "local":
        root = os.path.realpath(os.path.abspath(project_root))
        home = os.path.realpath(os.path.expanduser("~"))
        if url.startswith("file://"):
            path = url[7:].strip()
            path = unquote(path)
            if not path:
                return None, "Invalid file URL", 400
        else:
            # Bare path (e.g. .agent-trace/conversations/xyz.json) — use as-is, resolve below
            path = url
        # Resolve relative paths against project root
        if not os.path.isabs(path):
            full = os.path.normpath(os.path.join(root, path.lstrip("/")))
        else:
            full = os.path.normpath(path)
        full = os.path.realpath(full)
        # Allow if under project root or under user's home (e.g. ~/.cursor/.../agent-transcripts/)
        if not full.startswith(root) and not full.startswith(home):
            return None, "Conversation file is outside project or home directory", 403
        if not os.path.isfile(full):
            return None, "Conversation file not found", 404
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            return None, "Could not read conversation file", 404
        return {"content": content}, None, 200

    # Remote mode: fetch from agent-trace-service
    if storage != "remote":
        return None, "Unsupported storage mode", 400
    project_id = config.get("project_id")
    auth_token = _get_auth_token(config)
    service_url = _get_service_url(config)
    if not project_id or not auth_token:
        return None, "Remote mode requires project_id and auth token in .agent-trace/config.json", 400
    req_url = f"{service_url}/api/v1/conversations/content?project_id={urllib.parse.quote(project_id)}&url={urllib.parse.quote(url)}"
    req = urllib.request.Request(req_url, method="GET")
    req.add_header("Authorization", f"Bearer {auth_token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        content = data.get("content")
        if content is None:
            return None, "Conversation not found", 404
        return {"content": content}, None, 200
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        try:
            err = json.loads(body).get("error", body)
        except (ValueError, TypeError):
            err = body or e.reason
        if e.code == 404:
            return None, err or "Conversation not found", 404
        return None, err or f"Service error {e.code}", 502
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, f"Could not reach agent-trace service: {e}", 502
