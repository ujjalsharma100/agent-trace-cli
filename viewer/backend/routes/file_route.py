"""
/api/file — read file content (relative to project root).
Returns 404 for binary or missing files.
"""
from __future__ import annotations

import os

# Common binary extensions / patterns; skip sending as text
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".pyc", ".so", ".dll",
    ".exe", ".class", ".jar", ".bin",
})


def safe_read_file(project_root: str, rel_path: str) -> tuple[str | None, str | None]:
    """
    Read file at project_root/rel_path if it's text.
    Returns (content, content_type) or (None, None) if not found or binary.
    content_type is e.g. "text/plain" or "application/json".
    """
    root = os.path.abspath(project_root)
    full = os.path.normpath(os.path.join(root, rel_path.lstrip("/")))
    if not full.startswith(root):
        return None, None
    if not os.path.isfile(full):
        return None, None
    ext = os.path.splitext(full)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return None, None
    try:
        with open(full, "rb") as f:
            raw = f.read()
    except OSError:
        return None, None
    # Heuristic: if null byte, treat as binary
    if b"\x00" in raw:
        return None, None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, None
    if ".json" in full or ext == ".json":
        return text, "application/json"
    return text, "text/plain; charset=utf-8"
