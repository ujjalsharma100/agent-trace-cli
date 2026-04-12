"""
Git notes for agent-trace (``refs/notes/agent-trace``).

Attaches JSON metadata per commit with composable sections (core, ledger, summary, prompts).
Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from typing import Any, Iterator

from .models import Ledger, Trace

NOTE_REF = "agent-trace"


def _git(*args: str, cwd: str | None = None) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def resolve_commit(repo_dir: str, rev: str) -> str | None:
    """Resolve ``rev`` (e.g. ``HEAD``) to a full SHA."""
    return _git("rev-parse", rev, cwd=repo_dir)


def ledger_dict_hash(ledger: dict[str, Any]) -> str:
    """Canonical SHA-256 over the ledger JSON (sorted keys)."""
    body = json.dumps(ledger, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def _ledger_to_dict(ledger: Ledger | dict[str, Any]) -> dict[str, Any]:
    if isinstance(ledger, Ledger):
        return ledger.to_dict()
    return dict(ledger)


def _stats_from_ledger(ledger: dict[str, Any]) -> dict[str, int]:
    ai = human = mixed = 0
    for _fp, fl in ledger.get("files", {}).items():
        if not isinstance(fl, dict):
            continue
        for seg in fl.get("line_attributions", []):
            if not isinstance(seg, dict):
                continue
            t = str(seg.get("type", "")).lower()
            start = int(seg.get("start_line", 0))
            end = int(seg.get("end_line", 0))
            n = max(0, end - start + 1) if start and end else 0
            if t == "ai":
                ai += n
            elif t == "mixed":
                mixed += n
            elif t == "human":
                human += n
    return {"ai_lines": ai, "human_lines": human, "mixed_lines": mixed}


def _prompts_from_traces(traces: list[Trace], *, max_chars: int = 500) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tr in traces:
        meta = tr.metadata or {}
        for key in ("prompt", "user_prompt", "instruction"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                s = val.strip()[:max_chars]
                if s not in seen:
                    seen.add(s)
                    out.append(s)
                break
    return out


def build_note(
    ledger: Ledger | dict[str, Any],
    traces: list[Trace],
    *,
    include_ledger: bool,
    include_summary: bool,
    include_prompts: bool,
    summaries: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the note JSON. Core fields always present; optional sections per flags."""
    led = _ledger_to_dict(ledger)
    trace_ids = [str(x) for x in led.get("trace_ids", [])]
    h = ledger_dict_hash(led)
    stats = _stats_from_ledger(led)
    note: dict[str, Any] = {
        "version": "1.0",
        "trace_ids": trace_ids,
        "ledger_hash": h,
        "stats": stats,
    }
    if include_ledger:
        files = led.get("files", {})
        if isinstance(files, dict) and files:
            note["ledger"] = {"files": {k: v for k, v in files.items()}}
    if include_summary and summaries:
        note["summary"] = dict(summaries)
    if include_prompts:
        pr = _prompts_from_traces(traces)
        if pr:
            note["prompts"] = pr
    return note


def attach_note(commit_sha: str, note: dict[str, Any], repo_dir: str) -> bool:
    """Attach JSON note to ``commit_sha`` on ``refs/notes/agent-trace``."""
    if not commit_sha or not repo_dir:
        return False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            suffix=".json",
        ) as tf:
            json.dump(note, tf, ensure_ascii=False)
            path = tf.name
        try:
            r = subprocess.run(
                [
                    "git",
                    "notes",
                    "--ref",
                    NOTE_REF,
                    "add",
                    "-f",
                    "-F",
                    path,
                    commit_sha,
                ],
                capture_output=True,
                text=True,
                cwd=repo_dir,
                timeout=60,
            )
            return r.returncode == 0
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        return False
    return False


def read_note(commit_sha: str, repo_dir: str) -> dict[str, Any] | None:
    """Read and parse the agent-trace note for ``commit_sha``, or None."""
    raw = _git("notes", "--ref", NOTE_REF, "show", commit_sha, cwd=repo_dir)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def list_notes(repo_dir: str, range_spec: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(commit_sha, note_dict)`` for commits in ``range_spec`` that have a note.

    ``range_spec`` is passed to ``git rev-list`` (e.g. ``HEAD~3..HEAD``). Default ``HEAD``.
    """
    spec = range_spec if range_spec else "HEAD"
    out = _git("rev-list", spec, cwd=repo_dir)
    if not out:
        return iter(())
    shas = [ln.strip() for ln in out.splitlines() if ln.strip()]

    def _gen() -> Iterator[tuple[str, dict[str, Any]]]:
        for sha in shas:
            n = read_note(sha, repo_dir)
            if n:
                yield (sha, n)

    return _gen()


def strip_sections(commit_sha: str, sections: list[str], repo_dir: str) -> bool:
    """Remove optional sections (``ledger``, ``summary``, ``prompts``) from the note."""
    note = read_note(commit_sha, repo_dir)
    if not note:
        return False
    for sec in sections:
        if sec in ("ledger", "summary", "prompts"):
            note.pop(sec, None)
    return attach_note(commit_sha, note, repo_dir)


def _notes_config(project_dir: str) -> dict[str, Any]:
    from .config import get_project_config

    cfg = get_project_config(project_dir)
    if not cfg:
        return {}
    raw = cfg.get("notes")
    return raw if isinstance(raw, dict) else {}


def project_notes_flags(project_dir: str) -> tuple[bool, bool, bool]:
    """Default ``(include_ledger, include_summary, include_prompts)`` from project config."""
    nc = _notes_config(project_dir)
    return (
        bool(nc.get("include_ledger", True)),
        bool(nc.get("include_summary", False)),
        bool(nc.get("include_prompts", False)),
    )


def _load_local_traces_raw(project_dir: str) -> list[dict[str, Any]]:
    """Same as commit_link._load_local_traces (duplicated to avoid import cycle)."""
    import json

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


def load_traces_for_ids(project_dir: str, trace_ids: list[str]) -> list[Trace]:
    if not trace_ids:
        return []
    want = set(trace_ids)
    out: list[Trace] = []
    for row in _load_local_traces_raw(project_dir):
        tid = row.get("id")
        if tid and str(tid) in want:
            try:
                out.append(Trace.from_dict(row))
            except Exception:
                continue
    return out


def attach_note_after_ledger(project_dir: str, ledger: dict[str, Any]) -> bool:
    """After a ledger is stored: build and attach a git note per project ``notes.*`` config."""
    try:
        nc = _notes_config(project_dir)
        if nc.get("enabled") is False:
            return False
        include_ledger = bool(nc.get("include_ledger", True))
        include_summary = bool(nc.get("include_summary", False))
        include_prompts = bool(nc.get("include_prompts", False))
        summaries = nc.get("summaries") if isinstance(nc.get("summaries"), dict) else None

        tid_list = [str(x) for x in ledger.get("trace_ids", [])]
        traces = load_traces_for_ids(project_dir, tid_list)
        note = build_note(
            ledger,
            traces,
            include_ledger=include_ledger,
            include_summary=include_summary,
            include_prompts=include_prompts,
            summaries=summaries,
        )
        sha = str(ledger.get("commit_sha", ""))
        if not sha:
            return False
        return attach_note(sha, note, project_dir)
    except Exception:
        return False


def rebuild_notes_for_range(
    project_dir: str,
    range_spec: str,
    *,
    include_ledger: bool | None = None,
    include_summary: bool | None = None,
    include_prompts: bool | None = None,
) -> int:
    """Rebuild notes from local ledgers for commits in ``range_spec``. Returns count updated."""
    from .ledger import load_local_ledgers

    nc = _notes_config(project_dir)
    il = include_ledger if include_ledger is not None else bool(nc.get("include_ledger", True))
    isum = include_summary if include_summary is not None else bool(nc.get("include_summary", False))
    ipr = include_prompts if include_prompts is not None else bool(nc.get("include_prompts", False))
    summaries = nc.get("summaries") if isinstance(nc.get("summaries"), dict) else None

    ledgers = load_local_ledgers(project_dir)
    out = _git("rev-list", range_spec, cwd=project_dir)
    if not out:
        return 0
    shas = [ln.strip() for ln in out.splitlines() if ln.strip()]
    count = 0
    for sha in shas:
        led = ledgers.get(sha)
        if not led:
            continue
        tid_list = [str(x) for x in led.get("trace_ids", [])]
        traces = load_traces_for_ids(project_dir, tid_list)
        note = build_note(
            led,
            traces,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            summaries=summaries,
        )
        if attach_note(sha, note, project_dir):
            count += 1
    return count


def backfill_notes(
    project_dir: str,
    *,
    since: str | None = None,
    include_ledger: bool | None = None,
    include_summary: bool | None = None,
    include_prompts: bool | None = None,
) -> int:
    """Rebuild notes for commits touching the repo since a date (``git rev-list --since``)."""
    parts: list[str] = ["rev-list"]
    if since:
        parts.extend(["--since", since])
    parts.append("HEAD")
    out = _git(*parts, cwd=project_dir)
    if not out:
        return 0
    shas = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not shas:
        return 0
    # rev-list outputs oldest-first; rebuild_range needs a single spec — use chained parents
    # Simplest: rebuild each sha (inefficient but clear)
    from .ledger import load_local_ledgers

    nc = _notes_config(project_dir)
    il = include_ledger if include_ledger is not None else bool(nc.get("include_ledger", True))
    isum = include_summary if include_summary is not None else bool(nc.get("include_summary", False))
    ipr = include_prompts if include_prompts is not None else bool(nc.get("include_prompts", False))
    summaries = nc.get("summaries") if isinstance(nc.get("summaries"), dict) else None

    ledgers = load_local_ledgers(project_dir)
    count = 0
    for sha in shas:
        led = ledgers.get(sha)
        if not led:
            continue
        tid_list = [str(x) for x in led.get("trace_ids", [])]
        traces = load_traces_for_ids(project_dir, tid_list)
        note = build_note(
            led,
            traces,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            summaries=summaries,
        )
        if attach_note(sha, note, project_dir):
            count += 1
    return count


def git_notes_push(repo_dir: str, remote: str = "origin") -> tuple[bool, str]:
    """Run ``git push`` for the agent-trace notes ref."""
    try:
        r = subprocess.run(
            [
                "git",
                "push",
                remote,
                f"refs/notes/{NOTE_REF}:refs/notes/{NOTE_REF}",
            ],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=120,
        )
        msg = (r.stderr or r.stdout or "").strip()
        return r.returncode == 0, msg or "ok"
    except Exception as e:
        return False, str(e)


def git_notes_pull(repo_dir: str, remote: str = "origin") -> tuple[bool, str]:
    """Run ``git fetch`` for the agent-trace notes ref."""
    try:
        r = subprocess.run(
            [
                "git",
                "fetch",
                remote,
                f"refs/notes/{NOTE_REF}:refs/notes/{NOTE_REF}",
            ],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=120,
        )
        msg = (r.stderr or r.stdout or "").strip()
        return r.returncode == 0, msg or "ok"
    except Exception as e:
        return False, str(e)


def ledger_from_note_for_blame(note: dict[str, Any]) -> dict[str, Any] | None:
    """Return a minimal ledger dict for blame if the note embeds ``ledger``."""
    led = note.get("ledger")
    if not isinstance(led, dict):
        return None
    files = led.get("files")
    if not isinstance(files, dict) or not files:
        return None
    return {"files": files}
