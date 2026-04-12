"""Trace metadata for ledger enrichment (internal)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_local_traces(project_dir: str) -> list[dict[str, Any]]:
    from .storage import get_traces_path, resolve_project_id

    pid = resolve_project_id(project_dir, create=False)
    if not pid:
        return []
    traces_path = get_traces_path(pid)
    if not traces_path.exists():
        return []
    traces: list[dict[str, Any]] = []
    try:
        for line in traces_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return traces


def _load_conversation_summary(url: str | None, max_chars: int = 200) -> str | None:
    if not url or not url.startswith("file://"):
        return None
    local_path = url[7:]
    try:
        with open(local_path, "r") as f:
            content = f.read(max_chars + 100)
        content = content.strip()
        if not content:
            return None
        if len(content) > max_chars:
            content = content[:max_chars] + "..."
        return content
    except (OSError, IOError):
        return None


def _find_matching_file(files: list[dict[str, Any]], file_path: str) -> dict[str, Any] | None:
    for f in files:
        if not isinstance(f, dict):
            continue
        trace_path = f.get("path", "")
        if trace_path == file_path:
            return f
        if trace_path.endswith(file_path) or file_path.endswith(trace_path):
            return f
    return None


def _collect_ranges(file_entry: dict[str, Any]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    if "start_line" in file_entry and "end_line" in file_entry:
        try:
            ranges.append((int(file_entry["start_line"]), int(file_entry["end_line"])))
        except (ValueError, TypeError):
            pass
    for conv in file_entry.get("conversations", []):
        if not isinstance(conv, dict):
            continue
        if "start_line" in conv and "end_line" in conv:
            try:
                ranges.append((int(conv["start_line"]), int(conv["end_line"])))
            except (ValueError, TypeError):
                pass
        for r in conv.get("ranges", []):
            if isinstance(r, dict) and "start_line" in r and "end_line" in r:
                try:
                    ranges.append((int(r["start_line"]), int(r["end_line"])))
                except (ValueError, TypeError):
                    pass
    for change in file_entry.get("changes", []):
        if not isinstance(change, dict):
            continue
        if "start_line" in change and "end_line" in change:
            try:
                ranges.append((int(change["start_line"]), int(change["end_line"])))
            except (ValueError, TypeError):
                pass
    return ranges


def _extract_trace_meta(
    trace: dict[str, Any],
    file_path: str,
    line_number: int,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"trace_id": trace.get("id")}
    if trace.get("timestamp"):
        meta["timestamp"] = trace["timestamp"]
    tool = trace.get("tool")
    if isinstance(tool, dict):
        meta["tool"] = tool
    files_data = trace.get("files") or []
    matched_file = _find_matching_file(files_data, file_path)
    if matched_file:
        for conv in matched_file.get("conversations", []):
            if not isinstance(conv, dict):
                continue
            contributor = conv.get("contributor") or {}
            if contributor.get("model_id") and not meta.get("model_id"):
                meta["model_id"] = contributor["model_id"]
            if contributor.get("type") and not meta.get("contributor_type"):
                meta["contributor_type"] = contributor["type"]
            if conv.get("url") and not meta.get("conversation_url"):
                meta["conversation_url"] = conv["url"]
            if meta.get("model_id") and meta.get("conversation_url"):
                break
        ranges = _collect_ranges(matched_file)
        best = None
        best_dist = float("inf")
        for start, end in ranges:
            if start <= line_number <= end:
                span = end - start
                if best is None or span < (best[1] - best[0]):
                    best = (start, end)
                    best_dist = 0
            else:
                dist = min(abs(line_number - start), abs(line_number - end))
                if dist < best_dist:
                    best = (start, end)
                    best_dist = dist
        if best:
            meta["matched_range"] = {"start_line": best[0], "end_line": best[1]}
    if not meta.get("model_id") or not meta.get("conversation_url"):
        for fe in files_data:
            if not isinstance(fe, dict) or fe is matched_file:
                continue
            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not meta.get("model_id"):
                    meta["model_id"] = contributor["model_id"]
                if conv.get("url") and not meta.get("conversation_url"):
                    meta["conversation_url"] = conv["url"]
            if meta.get("model_id") and meta.get("conversation_url"):
                break
    return meta
