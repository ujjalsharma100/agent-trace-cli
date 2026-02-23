"""
/api/tree — list directories and files under a path (relative to project root).
Respects .gitignore and hides .git by default.
"""
from __future__ import annotations

import os


def _read_gitignore(project_root: str) -> set[str]:
    """Read .gitignore and return set of patterns (simplified: line-based)."""
    path = os.path.join(project_root, ".gitignore")
    if not os.path.isfile(path):
        return set()
    seen = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    seen.add(line)
    except OSError:
        pass
    return seen


def _ignored(name: str, gitignore: set[str], dir_path: str, project_root: str) -> bool:
    """True if name should be ignored (e.g. by .gitignore or .git)."""
    if name == ".git" or name.startswith("."):
        return True
    # Relative path for gitignore matching (from project root)
    rel = os.path.relpath(os.path.join(dir_path, name), project_root)
    for pattern in gitignore:
        if pattern.startswith("/"):
            if rel == pattern[1:] or rel.startswith(pattern[1:].rstrip("/") + "/"):
                return True
        else:
            if rel == pattern or rel.endswith("/" + pattern) or pattern in rel.split("/"):
                return True
    return False


def get_tree(project_root: str, rel_path: str) -> list[dict]:
    """
    List entries under project_root/rel_path.
    Returns list of { name, path, type: "dir"|"file" }.
    path is relative to project root.
    """
    root = os.path.abspath(project_root)
    full = os.path.normpath(os.path.join(root, rel_path.lstrip("/")))
    if not full.startswith(root):
        return []
    if not os.path.isdir(full):
        return []
    gitignore = _read_gitignore(root)
    entries = []
    try:
        for name in sorted(os.listdir(full)):
            child_full = os.path.join(full, name)
            if _ignored(name, gitignore, full, root):
                continue
            rel = os.path.relpath(child_full, root)
            if os.path.isdir(child_full):
                entries.append({"name": name, "path": rel, "type": "dir"})
            else:
                entries.append({"name": name, "path": rel, "type": "file"})
    except OSError:
        pass
    return entries
