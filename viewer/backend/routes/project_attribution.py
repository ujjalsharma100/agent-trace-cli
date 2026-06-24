"""
/api/project-attribution — aggregate line-level attribution across all tracked files.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def _git_ls_files(project_root: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            capture_output=True,
            cwd=project_root,
            timeout=60,
        )
        if result.returncode != 0:
            return []
        raw = result.stdout.decode("utf-8", errors="replace")
        return [p for p in raw.split("\0") if p]
    except Exception:
        return []


def _line_count(path: str) -> int:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return 0
    if not data:
        return 0
    if b"\0" in data[:8192]:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _is_no_attribution(attr: dict[str, Any]) -> bool:
    if attr.get("kind") == "NO_ATTRIBUTION":
        return True
    if attr.get("kind") == "AI":
        return False
    trace_id = attr.get("trace_id")
    has_trace = trace_id is not None and trace_id != ""
    if has_trace and attr.get("attribution_label") == "AI":
        return False
    return True


def _tool_key(tool: Any) -> str:
    if not tool:
        return ""
    if isinstance(tool, dict):
        name = tool.get("name") or ""
        version = tool.get("version") or ""
        return f"{name}@{version}" if version else name
    return str(tool)


def _legend_key(attr: dict[str, Any]) -> str:
    if _is_no_attribution(attr):
        return "No attribution"
    model = attr.get("model_id") or "(unknown model)"
    tk = _tool_key(attr.get("tool"))
    return f"AI:{model}:{tk}" if tk else f"AI:{model}"


def _count_distinct_lines(attributions: list[dict[str, Any]]) -> int:
    covered: set[int] = set()
    for attr in attributions:
        start = attr.get("start_line")
        end = attr.get("end_line")
        if start is None or end is None:
            continue
        for line in range(int(start), int(end) + 1):
            covered.add(line)
    return len(covered)


def _normalize_tool(tool: Any) -> dict[str, str] | None:
    if not tool:
        return None
    if isinstance(tool, dict):
        return {"name": tool.get("name") or "", "version": tool.get("version") or ""}
    return {"name": str(tool), "version": ""}


def get_project_attribution(
    project_root: str,
) -> tuple[dict[str, Any] | None, str | None, int]:
    root = os.path.abspath(project_root)
    tracked = _git_ls_files(root)
    if not tracked:
        return None, "not a git repository or no tracked files", 404

    from .agent_trace_blame import get_agent_trace_blame

    line_counts: dict[str, int] = {}
    total_lines = 0
    files_scanned = 0

    for rel_path in tracked:
        full = os.path.join(root, rel_path)
        if not os.path.isfile(full):
            continue
        n_lines = _line_count(full)
        if n_lines <= 0:
            continue
        line_counts[rel_path] = n_lines
        total_lines += n_lines

    bucket_lines: dict[str, int] = {}
    bucket_meta: dict[str, dict[str, Any]] = {}

    for rel_path, n_lines in line_counts.items():
        data, err, status = get_agent_trace_blame(root, rel_path)
        if status == 503:
            return None, err, 503
        files_scanned += 1
        if data is None:
            bucket_lines["No attribution"] = bucket_lines.get("No attribution", 0) + n_lines
            bucket_meta.setdefault("No attribution", {"key": "No attribution", "label": "No attribution"})
            continue

        attributions = data.get("attributions") or []

        if not attributions:
            bucket_lines["No attribution"] = bucket_lines.get("No attribution", 0) + n_lines
            bucket_meta.setdefault("No attribution", {"key": "No attribution", "label": "No attribution"})
            continue

        key_to_attrs: dict[str, list[dict[str, Any]]] = {}
        for attr in attributions:
            key = _legend_key(attr)
            if key == "No attribution":
                continue
            key_to_attrs.setdefault(key, []).append(attr)
            if key not in bucket_meta:
                bucket_meta[key] = {
                    "key": key,
                    "label": "AI",
                    "model_id": attr.get("model_id") or "(unknown model)",
                    "tool": _normalize_tool(attr.get("tool")),
                }

        attributed_lines = _count_distinct_lines(
            [a for a in attributions if not _is_no_attribution(a)]
        )
        no_attr_lines = max(0, n_lines - attributed_lines)
        if no_attr_lines > 0:
            bucket_lines["No attribution"] = bucket_lines.get("No attribution", 0) + no_attr_lines
            bucket_meta.setdefault("No attribution", {"key": "No attribution", "label": "No attribution"})

        for key, attrs in key_to_attrs.items():
            bucket_lines[key] = bucket_lines.get(key, 0) + _count_distinct_lines(attrs)

    breakdown: list[dict[str, Any]] = []
    for key, lines in sorted(bucket_lines.items(), key=lambda kv: (-kv[1], kv[0])):
        meta = bucket_meta.get(key, {"key": key, "label": key})
        entry = {**meta, "lines": lines}
        breakdown.append(entry)

    return {
        "total_lines": total_lines,
        "file_count": len(tracked),
        "files_scanned": files_scanned,
        "breakdown": breakdown,
    }, None, 200
