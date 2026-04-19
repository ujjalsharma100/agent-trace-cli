"""
/api/conversation — fetch full conversation content by URL or path.

GET ?url=...
Behavior:
  - If url is http:// or https://: return { "open_external": true, "url": "<url>" } so frontend opens in new tab.
  - Otherwise read from filesystem. Accept file://... (absolute or relative) or bare path.
    Absolute paths (e.g. file:///Users/.../.cursor/.../agent-transcripts/xxx.txt) are allowed if under
    project root or under the user's home directory. Relative paths are resolved against project root.
"""
from __future__ import annotations

import os
from urllib.parse import unquote


def get_conversation_content(project_root: str, url: str) -> tuple[dict | None, str | None, int]:
    """
    Resolve conversation content or action based on URL.

    Returns (result_dict, error_message, status_code).
    - ({ "content": "..." }, None, 200) — show content in modal
    - ({ "open_external": true, "url": "..." }, None, 200) — open URL in new tab
    - (None, message, 400/403/404) on failure
    """
    if not url or not isinstance(url, str):
        return None, "url required", 400
    url = unquote(url.strip())
    if not url:
        return None, "url required", 400

    # External URL (e.g. Cursor site) — tell frontend to open in new tab
    if url.startswith("https://") or url.startswith("http://"):
        return {"open_external": True, "url": url}, None, 200

    # Local mode: read from filesystem. Accept file:// URL (absolute or relative) or bare path.
    # Absolute paths (e.g. file:///Users/.../.cursor/.../agent-transcripts/xxx.txt) are allowed
    # if under project root or under the user's home directory (Cursor stores transcripts there).
    root = os.path.realpath(os.path.abspath(project_root))
    home = os.path.realpath(os.path.expanduser("~"))
    if url.startswith("file://"):
        path = url[7:].strip()
        path = unquote(path)
        if not path:
            return None, "Invalid file URL", 400
    else:
        # Bare path — resolve relative to project root
        path = url
    if not os.path.isabs(path):
        full = os.path.normpath(os.path.join(root, path.lstrip("/")))
    else:
        full = os.path.normpath(path)
    full = os.path.realpath(full)
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
