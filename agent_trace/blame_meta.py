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


def _load_conversation_preview(
    project_id: str | None,
    content_sha256: str | None,
    max_chars: int = 200,
) -> str | None:
    """First ``max_chars`` of the cached transcript bytes referenced by
    ``content_sha256``. Distinct from a generated summary — this is just a
    raw head-of-blob preview used as a fallback when no summary is
    configured/available.
    """
    if not project_id or not content_sha256:
        return None
    from .conversations import cache_path_for_sha

    p = cache_path_for_sha(project_id, content_sha256)
    if not p.is_file():
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 100)
    except OSError:
        return None
    content = content.strip()
    if not content:
        return None
    if len(content) > max_chars:
        content = content[:max_chars] + "..."
    return content


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
            if conv.get("id") and not meta.get("conversation_id"):
                meta["conversation_id"] = conv["id"]
            if conv.get("content_sha256") and not meta.get("content_sha256"):
                meta["content_sha256"] = conv["content_sha256"]
            if meta.get("model_id") and meta.get("conversation_id"):
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
    if not meta.get("model_id") or not meta.get("conversation_id"):
        for fe in files_data:
            if not isinstance(fe, dict) or fe is matched_file:
                continue
            for conv in fe.get("conversations", []):
                if not isinstance(conv, dict):
                    continue
                contributor = conv.get("contributor") or {}
                if contributor.get("model_id") and not meta.get("model_id"):
                    meta["model_id"] = contributor["model_id"]
                if conv.get("id") and not meta.get("conversation_id"):
                    meta["conversation_id"] = conv["id"]
                if conv.get("content_sha256") and not meta.get("content_sha256"):
                    meta["content_sha256"] = conv["content_sha256"]
            if meta.get("model_id") and meta.get("conversation_id"):
                break
    return meta
