"""
/api/git-blame — run git blame --porcelain and return segments.

Returns segments: [{ start_line, end_line, author, author_time, summary, commit_sha }].
Path must be under project_root. Uses stdlib only (subprocess).
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def _git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _parse_blame_porcelain(raw: str) -> list[dict[str, Any]]:
    """Parse git blame --porcelain output into per-line records.

    Each record: commit_sha, orig_line, final_line, content, author, author_time, summary, filename.
    """
    lines = raw.split("\n")
    records: list[dict[str, Any]] = []
    commit_info: dict[str, dict[str, Any]] = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue

        parts = line.split()
        if len(parts) < 3:
            i += 1
            continue

        sha = parts[0]
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            i += 1
            continue

        orig_line = int(parts[1])
        final_line = int(parts[2])
        i += 1

        if sha not in commit_info:
            info: dict[str, Any] = {}
            while i < len(lines):
                hline = lines[i]
                if hline.startswith("\t"):
                    break
                if hline.startswith("author "):
                    info["author"] = hline[7:]
                elif hline.startswith("author-time "):
                    try:
                        info["author_time"] = int(hline[12:])
                    except ValueError:
                        pass
                elif hline.startswith("summary "):
                    info["summary"] = hline[8:]
                elif hline.startswith("filename "):
                    info["filename"] = hline[9:]
                i += 1
            commit_info[sha] = info
        else:
            while i < len(lines) and not lines[i].startswith("\t"):
                hline = lines[i]
                if hline.startswith("filename "):
                    commit_info[sha]["filename"] = hline[9:]
                i += 1

        content = ""
        if i < len(lines) and lines[i].startswith("\t"):
            content = lines[i][1:]
            i += 1

        info = commit_info.get(sha, {})
        records.append({
            "commit_sha": sha,
            "orig_line": orig_line,
            "final_line": final_line,
            "content": content,
            "author": info.get("author", ""),
            "author_time": info.get("author_time"),
            "summary": info.get("summary", ""),
            "filename": info.get("filename", ""),
        })

    return records


def _group_into_segments(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive records that share the same commit SHA.

    Returns segments: { start_line, end_line, author, author_time, summary, commit_sha }.
    """
    if not records:
        return []

    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for rec in records:
        if (
            current is not None
            and current["commit_sha"] == rec["commit_sha"]
            and current["end_line"] + 1 == rec["final_line"]
        ):
            current["end_line"] = rec["final_line"]
        else:
            if current is not None:
                segments.append({
                    "start_line": current["start_line"],
                    "end_line": current["end_line"],
                    "author": current["author"],
                    "author_time": current["author_time"],
                    "summary": current["summary"],
                    "commit_sha": current["commit_sha"],
                })
            current = {
                "commit_sha": rec["commit_sha"],
                "start_line": rec["final_line"],
                "end_line": rec["final_line"],
                "author": rec.get("author", ""),
                "author_time": rec.get("author_time"),
                "summary": rec.get("summary", ""),
            }

    if current is not None:
        segments.append({
            "start_line": current["start_line"],
            "end_line": current["end_line"],
            "author": current["author"],
            "author_time": current["author_time"],
            "summary": current["summary"],
            "commit_sha": current["commit_sha"],
        })

    return segments


def get_git_blame(project_root: str, rel_path: str) -> list[dict[str, Any]] | None:
    """
    Run git blame --porcelain for the file at rel_path under project_root.

    - Resolves path under project_root; returns None if outside root or not a file.
    - Runs git in project_root; returns None if not a git repo or blame fails.
    - Returns list of segments: [{ start_line, end_line, author, author_time, summary, commit_sha }].
    """
    root = os.path.abspath(project_root)
    full = os.path.normpath(os.path.join(root, rel_path.lstrip("/")))
    if not full.startswith(root) or not os.path.isfile(full):
        return None

    git_root = _git("rev-parse", "--show-toplevel", cwd=project_root)
    if git_root is None:
        return None

    try:
        file_rel = os.path.relpath(full, git_root)
    except ValueError:
        file_rel = rel_path.lstrip("/")

    raw = _git("blame", "--porcelain", file_rel, cwd=git_root)
    if not raw:
        return None

    records = _parse_blame_porcelain(raw)
    if not records:
        return None

    return _group_into_segments(records)
