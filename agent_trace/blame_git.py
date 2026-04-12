"""Git blame --porcelain parsing and segment grouping (internal)."""

from __future__ import annotations

import subprocess
from typing import Any


def _git(*args: str, cwd: str | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _git_blame_porcelain(
    file_path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    cwd: str | None = None,
) -> str | None:
    args = ["blame", "--porcelain"]
    if start_line is not None and end_line is not None:
        args.extend(["-L", f"{start_line},{end_line}"])
    elif start_line is not None:
        args.extend(["-L", f"{start_line},{start_line}"])
    args.append(file_path)
    return _git(*args, cwd=cwd)


def _parse_blame_porcelain(raw: str) -> list[dict[str, Any]]:
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
            current["orig_end_line"] = rec["orig_line"]
            current["content_lines"].append(rec["content"])
        else:
            if current is not None:
                segments.append(current)
            current = {
                "commit_sha": rec["commit_sha"],
                "start_line": rec["final_line"],
                "end_line": rec["final_line"],
                "orig_start_line": rec["orig_line"],
                "orig_end_line": rec["orig_line"],
                "content_lines": [rec["content"]],
                "author": rec.get("author", ""),
                "author_time": rec.get("author_time"),
                "summary": rec.get("summary", ""),
                "filename": rec.get("filename", ""),
            }
    if current is not None:
        segments.append(current)
    return segments
