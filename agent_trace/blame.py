"""
CLI blame command — show AI attribution for file lines.

Deterministic only: per-commit ledger (``.agent-trace/ledgers.jsonl``) plus
UNKNOWN when no ledger covers the line. No heuristics or remote blame API.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .blame_git import _git, _git_blame_porcelain, _group_into_segments, _parse_blame_porcelain
from .blame_meta import (
    _extract_trace_meta,
    _load_conversation_summary,
    _load_local_traces,
)
from .ledger import load_local_ledgers


def _attribution_type_label(attr_type: str) -> str:
    return {"ai": "AI", "human": "Human", "mixed": "Mixed"}.get(attr_type, attr_type)


def _ledger_kind(attr_type: str) -> str:
    if attr_type == "ai":
        return "AI"
    if attr_type == "mixed":
        return "MIXED"
    if attr_type == "human":
        return "HUMAN"
    return "UNKNOWN"


def _ranges_overlap(
    attr_start: int, attr_end: int,
    seg_start: int, seg_end: int,
) -> bool:
    return attr_start <= seg_end and attr_end >= seg_start


def _attribute_from_ledger(
    blame_segments: list[dict[str, Any]],
    ledgers: dict[str, dict[str, Any]],
    file_path: str,
    traces: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attributed: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []

    trace_by_id: dict[str, dict[str, Any]] = {}
    if traces:
        for t in traces:
            tid = t.get("id")
            if tid:
                trace_by_id[tid] = t

    for seg in blame_segments:
        commit_sha = seg["commit_sha"]
        ledger = ledgers.get(commit_sha)

        if ledger and file_path in ledger.get("files", {}):
            file_ledger = ledger["files"][file_path]
            line_attrs = file_ledger.get("line_attributions", [])

            orig_start = seg.get("orig_start_line", seg["start_line"])
            orig_end = seg.get("orig_end_line", seg["end_line"])
            offset = seg["start_line"] - orig_start

            overlapping: list[tuple[int, int, dict[str, Any]]] = []
            for la in sorted(line_attrs, key=lambda x: x.get("start_line", 0)):
                la_start = la.get("start_line", 0)
                la_end = la.get("end_line", 0)
                if _ranges_overlap(la_start, la_end, orig_start, orig_end):
                    clamped_start = max(la_start, orig_start)
                    clamped_end = min(la_end, orig_end)
                    overlapping.append((clamped_start, clamped_end, la))

            if overlapping:
                for clamped_orig_start, clamped_orig_end, la in overlapping:
                    final_start = clamped_orig_start + offset
                    final_end = clamped_orig_end + offset
                    attr_type = la.get("type", "unknown")
                    kind = _ledger_kind(attr_type)
                    trace_id = la.get("trace_id")
                    trace_rec = trace_by_id.get(trace_id) if trace_id else None
                    label = _attribution_type_label(attr_type)
                    meta = (
                        _extract_trace_meta(trace_rec, file_path, (final_start + final_end) // 2)
                        if trace_rec
                        else {}
                    )
                    conv_url = la.get("conversation_url") or meta.get("conversation_url")
                    conv_summary = _load_conversation_summary(conv_url)

                    attributed.append({
                        "start_line": final_start,
                        "end_line": final_end,
                        "kind": kind,
                        "attribution_label": label,
                        "trace_id": trace_id,
                        "timestamp": trace_rec.get("timestamp") if trace_rec else None,
                        "model_id": la.get("model_id") or meta.get("model_id"),
                        "contributor_type": attr_type,
                        "tool": trace_rec.get("tool") if trace_rec else None,
                        "conversation_url": conv_url,
                        "conversation_summary": conv_summary,
                        "matched_range": {
                            "start_line": la.get("start_line"),
                            "end_line": la.get("end_line"),
                        },
                        "commit_sha": commit_sha,
                        "signals": ["ledger"],
                        "source": "ledger",
                    })

                covered_orig_start = overlapping[0][0]
                covered_orig_end = overlapping[-1][1]
                if orig_start < covered_orig_start:
                    gap_seg = dict(seg)
                    gap_seg["start_line"] = seg["start_line"]
                    gap_seg["end_line"] = covered_orig_start + offset - 1
                    gap_seg["orig_start_line"] = orig_start
                    gap_seg["orig_end_line"] = covered_orig_start - 1
                    n_lines = covered_orig_start - orig_start
                    gap_seg["content_lines"] = seg["content_lines"][:n_lines]
                    remaining.append(gap_seg)
                if covered_orig_end < orig_end:
                    gap_seg = dict(seg)
                    n_before = covered_orig_end - orig_start + 1
                    gap_seg["start_line"] = covered_orig_end + offset + 1
                    gap_seg["end_line"] = seg["end_line"]
                    gap_seg["orig_start_line"] = covered_orig_end + 1
                    gap_seg["orig_end_line"] = orig_end
                    gap_seg["content_lines"] = seg["content_lines"][n_before:]
                    remaining.append(gap_seg)
                continue

        remaining.append(seg)

    return attributed, remaining


def _unknown_entry(seg: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_line": seg["start_line"],
        "end_line": seg["end_line"],
        "kind": "UNKNOWN",
        "attribution_label": "Unknown",
        "trace_id": None,
        "timestamp": None,
        "model_id": None,
        "contributor_type": None,
        "tool": None,
        "conversation_url": None,
        "conversation_summary": None,
        "matched_range": None,
        "commit_sha": seg["commit_sha"],
        "signals": [],
        "source": None,
    }


def _attribute_deterministic(
    blame_segments: list[dict[str, Any]],
    file_path: str,
    ledgers: dict[str, dict[str, Any]],
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ledger_results: list[dict[str, Any]] = []
    remaining = blame_segments
    if ledgers:
        ledger_results, remaining = _attribute_from_ledger(
            blame_segments, ledgers, file_path, traces=traces,
        )

    unknown_results = [_unknown_entry(seg) for seg in remaining]

    all_results = ledger_results + unknown_results
    all_results.sort(key=lambda a: (a.get("start_line", 0), a.get("end_line", 0)))
    return all_results


def _merge_attributions(attributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not attributions:
        return []
    merged: list[dict[str, Any]] = []
    for entry in attributions:
        if merged:
            prev = merged[-1]
            if (
                prev["end_line"] + 1 >= entry["start_line"]
                and prev.get("kind") == entry.get("kind")
                and prev.get("trace_id") == entry.get("trace_id")
                and prev.get("source") == entry.get("source")
            ):
                prev["end_line"] = entry["end_line"]
                continue
        merged.append(dict(entry))
    return merged


_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _format_line_range(start: int, end: int) -> str:
    if start == end:
        return f"L{start}"
    return f"L{start}-{end}"


def _format_terminal(file_path: str, attributions: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(f"  {_BOLD}{file_path}{_RESET}")
    lines.append("")

    for attr in attributions:
        start = attr.get("start_line", 0)
        end = attr.get("end_line", 0)
        lr = _format_line_range(start, end)
        kind = attr.get("kind", "UNKNOWN")

        if kind == "AI":
            tag = f"{_GREEN}[AI]{_RESET}"
        elif kind == "MIXED":
            tag = f"{_YELLOW}[MIXED]{_RESET}"
        elif kind == "HUMAN":
            tag = f"{_DIM}[HUMAN]{_RESET}"
        else:
            tag = f"{_DIM}[UNKNOWN]{_RESET}"

        model_id = attr.get("model_id") or ""
        tool = attr.get("tool")
        tool_name = ""
        if isinstance(tool, dict):
            tool_name = tool.get("name", "")
        elif isinstance(tool, str):
            tool_name = tool

        model_tool = model_id
        if tool_name:
            model_tool = f"{model_id} via {tool_name}" if model_id else tool_name

        if kind == "UNKNOWN":
            lines.append(f"  {lr:<12}{tag}")
            continue

        lines.append(f"  {lr:<12}{tag} {model_tool}")

        if model_id:
            lines.append(f"              {_DIM}model: {model_id}{_RESET}")

        conv_summary = attr.get("conversation_summary") or ""
        conv_url = attr.get("conversation_url") or ""
        if conv_summary:
            summary_line = conv_summary.replace("\n", " ").strip()
            if len(summary_line) > 120:
                summary_line = summary_line[:120] + "..."
            lines.append(f"              conversation: \"{summary_line}\"")
        elif conv_url:
            lines.append(f"              conversation: {conv_url}")

        trace_id = attr.get("trace_id") or ""
        commit_sha = attr.get("commit_sha") or ""
        detail_parts = []
        if trace_id:
            detail_parts.append(f"trace: {trace_id}")
        if commit_sha:
            detail_parts.append(f"commit: {commit_sha[:12]}")
        if detail_parts:
            lines.append(f"              {_DIM}{' | '.join(detail_parts)}{_RESET}")

    lines.append("")
    return "\n".join(lines)


def _format_json(file_path: str, attributions: list[dict[str, Any]]) -> str:
    clean: list[dict[str, Any]] = []
    for attr in attributions:
        entry: dict[str, Any] = {
            "start_line": attr.get("start_line"),
            "end_line": attr.get("end_line"),
            "kind": attr.get("kind"),
        }
        if attr.get("trace_id"):
            entry["trace_id"] = attr["trace_id"]
        model_id = attr.get("model_id")
        if model_id:
            entry["model_id"] = model_id
        contributor_type = attr.get("contributor_type")
        if contributor_type:
            entry["contributor_type"] = contributor_type
        tool = attr.get("tool")
        if isinstance(tool, dict):
            entry["tool"] = {"name": tool.get("name", ""), "version": tool.get("version", "")}
        elif isinstance(tool, str):
            entry["tool"] = {"name": tool, "version": ""}
        if attr.get("timestamp"):
            entry["timestamp"] = attr["timestamp"]
        if attr.get("commit_sha"):
            entry["commit_sha"] = attr["commit_sha"]
        if attr.get("conversation_url"):
            entry["conversation_url"] = attr["conversation_url"]
        if attr.get("conversation_summary"):
            entry["conversation_summary"] = attr["conversation_summary"]
        if attr.get("signals"):
            entry["signals"] = attr["signals"]
        if attr.get("source"):
            entry["source"] = attr["source"]
        if attr.get("attribution_label"):
            entry["attribution_label"] = attr["attribution_label"]
        clean.append(entry)

    return json.dumps({"file": file_path, "attributions": clean}, indent=2)


def _filter_unknown(
    attributions: list[dict[str, Any]],
    show_unknown: bool,
) -> list[dict[str, Any]]:
    if show_unknown:
        return attributions
    return [a for a in attributions if a.get("kind") != "UNKNOWN"]


def _has_unknown(attributions: list[dict[str, Any]]) -> bool:
    return any(a.get("kind") == "UNKNOWN" for a in attributions)


def blame_file(
    file_path: str,
    *,
    line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    show_unknown: bool = False,
    require_attribution: bool = False,
    json_output: bool = False,
    project_dir: str | None = None,
) -> str | None:
    cwd = project_dir if project_dir else os.getcwd()
    abs_path = (
        os.path.abspath(file_path)
        if os.path.isabs(file_path)
        else os.path.abspath(os.path.join(cwd, file_path))
    )

    if not os.path.isfile(abs_path):
        if json_output:
            return None
        print(f"agent-trace blame: file not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    file_dir = os.path.dirname(abs_path) or cwd
    git_root = _git("rev-parse", "--show-toplevel", cwd=file_dir)
    if git_root is None:
        if json_output:
            return None
        print("agent-trace blame: not a git repository", file=sys.stderr)
        sys.exit(1)

    try:
        rel_path = os.path.relpath(abs_path, git_root)
    except ValueError:
        rel_path = file_path

    if line is not None:
        start_line = line
        end_line = line

    raw = _git_blame_porcelain(
        rel_path,
        start_line=start_line,
        end_line=end_line,
        cwd=git_root,
    )
    if raw is None:
        if json_output:
            return None
        print(f"agent-trace blame: git blame failed for {file_path}", file=sys.stderr)
        sys.exit(1)

    records = _parse_blame_porcelain(raw)
    if not records:
        if json_output:
            return None
        print(f"agent-trace blame: no blame data for {file_path}", file=sys.stderr)
        sys.exit(1)

    segments = _group_into_segments(records)

    traces = _load_local_traces(git_root)
    ledgers = load_local_ledgers(git_root)

    raw_attrs = _attribute_deterministic(segments, rel_path, ledgers, traces)
    attributions = _merge_attributions(raw_attrs)

    if require_attribution and _has_unknown(attributions):
        print(
            "agent-trace blame: unknown attribution for one or more lines "
            "(ledger missing or incomplete).",
            file=sys.stderr,
        )
        sys.exit(1)

    out = _filter_unknown(attributions, show_unknown)

    if json_output:
        return _format_json(rel_path, out)
    print(_format_terminal(rel_path, out))
    return None
