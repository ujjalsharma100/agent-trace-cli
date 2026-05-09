"""
Trace record construction helpers.

Builds trace records from hook event data following the agent-trace spec.
Resolution is file-anchored: the git repo owning the edited file, not the
agent's launch directory (Phase 1b).

No external dependencies — stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


# -------------------------------------------------------------------
# Project resolution (Phase 1b)
# -------------------------------------------------------------------


@dataclass
class ProjectResolution:
    """Owning repo (or detached bucket) for a path."""

    repo_root: str
    project_id: str
    rel_path: str
    vcs: dict | None
    is_detached: bool = False


def _git_out(args: list[str], cwd: str, timeout: float = 10.0) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def git_repo_root_for_path(path: str) -> str | None:
    """Innermost git work tree containing ``path`` (``git -C <dir> rev-parse --show-toplevel``)."""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        d = os.path.dirname(p)
    else:
        d = p if os.path.isdir(p) else os.path.dirname(p)
    if not os.path.isdir(d):
        return None
    out = _git_out(["rev-parse", "--show-toplevel"], d)
    if not out:
        return None
    return os.path.realpath(out)


def _is_pseudo_path(file_path: str) -> bool:
    """Hook placeholder paths (not real files)."""
    if not file_path or file_path.startswith("/"):
        return False
    norm = file_path.replace("\\", "/").strip("./")
    return norm in (
        "shell-history",
        "sessions",
        "unknown",
    ) or file_path in (".shell-history", ".sessions", ".unknown")


def resolve_file_project(
    file_path: str,
    *,
    anchor_path: str | None = None,
) -> ProjectResolution | None:
    """Resolve repo + project_id from the file (or anchor), not session cwd.

    ``anchor_path`` is used when ``file_path`` is a pseudo path (e.g. ``.shell-history``)
    or a relative path: it should be cwd or a file/dir inside the intended repo.
    """
    from .config import get_global_config
    from .registry import lookup_or_create_project_id

    fp = file_path
    if not os.path.isabs(fp) and anchor_path and not _is_pseudo_path(fp):
        an = os.path.abspath(anchor_path)
        base = os.path.dirname(an) if os.path.isfile(an) else an
        fp = os.path.normpath(os.path.join(base, fp))

    if _is_pseudo_path(file_path):
        if anchor_path:
            an = os.path.abspath(anchor_path)
            path_for_git = os.path.dirname(an) if os.path.isfile(an) else an
        else:
            path_for_git = os.getcwd()
        rel = file_path.replace(os.sep, "/")
    else:
        abs_fp = os.path.abspath(fp)
        path_for_git = abs_fp
        if os.path.isfile(abs_fp):
            pass
        elif os.path.isdir(abs_fp):
            path_for_git = os.path.join(abs_fp, ".")
        else:
            d = os.path.dirname(abs_fp)
            path_for_git = d if d else abs_fp

    repo_root = git_repo_root_for_path(path_for_git)
    if repo_root:
        if not _is_pseudo_path(file_path):
            real_file = os.path.realpath(os.path.abspath(fp))
            try:
                rel = os.path.relpath(real_file, repo_root)
            except ValueError:
                rel = os.path.basename(real_file)
        else:
            rel = file_path.replace(os.sep, "/")
        pid = lookup_or_create_project_id(repo_root)
        vcs = get_vcs_info(repo_root)
        return ProjectResolution(
            repo_root=repo_root,
            project_id=pid,
            rel_path=rel.replace(os.sep, "/"),
            vcs=vcs,
            is_detached=False,
        )

    # No git repo — optional detached capture
    cfg = get_global_config()
    if not cfg.get("capture_detached_edits"):
        return None

    if anchor_path:
        an = os.path.abspath(anchor_path)
        parent = os.path.dirname(an) if os.path.isfile(an) else an
    else:
        parent = os.path.dirname(os.path.realpath(os.path.abspath(fp)))
    parent = os.path.realpath(parent or os.getcwd())
    h = hashlib.sha256(parent.encode("utf-8")).hexdigest()[:16]
    pid = f"detached:{h}"
    from .storage import get_detached_base_dir

    base = os.path.realpath(str(get_detached_base_dir() / h))
    os.makedirs(base, exist_ok=True)
    rel_name = file_path.replace(os.sep, "/")
    if not _is_pseudo_path(file_path):
        rel_name = os.path.basename(os.path.realpath(os.path.abspath(fp)))
    return ProjectResolution(
        repo_root=base,
        project_id=pid,
        rel_path=rel_name,
        vcs=None,
        is_detached=True,
    )


def get_workspace_root() -> str:
    """Best-effort git root from current working directory (session-level hints).

    Does not use CURSOR/CLAUDE env vars for routing — prefer :func:`resolve_file_project`
    for file-backed traces.
    """
    g = git_repo_root_for_path(os.getcwd())
    if g:
        return g
    return os.getcwd()


def get_tool_info() -> dict:
    """Detect which AI coding tool invoked the hook.

    Walks the adapter registry — each ``CodingAgentAdapter`` may
    implement ``detect_tool_info`` to return ``{name, version}`` based
    on env vars or marker files. The first adapter that matches wins.
    """
    try:
        from .hooks import iter_adapters

        for adapter in iter_adapters():
            info = adapter.detect_tool_info()
            if info:
                return info
    except Exception:
        # Adapter detection must never crash recording.
        pass
    return {"name": "unknown"}


def get_vcs_info(cwd: str | None = None) -> dict | None:
    """Current git revision, or None."""
    root = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5,
        )
        if result.returncode == 0:
            return {"type": "git", "revision": result.stdout.strip()}
    except Exception:
        pass
    return None


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------

def to_relative_path(absolute_path: str, root: str) -> str:
    try:
        return os.path.relpath(absolute_path, root)
    except ValueError:
        return absolute_path


def normalize_model_id(model: str | None) -> str | None:
    """Add provider prefix to bare model names."""
    if not model:
        return None
    if "/" in model:
        return model
    prefixes = {
        "claude-": "anthropic",
        "gpt-": "openai",
        "o1": "openai",
        "o3": "openai",
        "gemini-": "google",
    }
    for prefix, provider in prefixes.items():
        if model.startswith(prefix):
            return f"{provider}/{model}"
    return model


def compute_line_hashes(content: str) -> list[dict]:
    """Compute per-line full SHA-256 hashes.

    Each line is hashed individually so the ledger can match committed lines
    back to the trace that produced them, even if surrounding lines shifted.
    Full 256-bit hashes guarantee cryptographic uniqueness, so no separate
    line content is stored — the hash alone is the line's identity.

    Returns a list of ``{"line_offset": 0, "hash": "sha256:..."}``.
    """
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    result: list[dict] = []
    for i, line in enumerate(lines):
        h = hashlib.sha256(line.encode("utf-8")).hexdigest()
        result.append({
            "line_offset": i,
            "hash": f"sha256:{h}",
        })
    return result


def compute_content_hash(content: str) -> str:
    """Full SHA-256 hash of normalized content for dedup / verification.

    Normalization: CRLF/CR → LF, and trailing newline stripped so that the
    same logical content hashes identically whether stored (e.g. tool
    new_string with trailing \\n) or matched (e.g. \"\\n\".join(blame lines)).
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def compute_range_positions(
    edits: list[dict],
    file_content: str | None = None,
) -> list[dict]:
    """Derive line-range positions from a list of edits."""
    positions: list[dict] = []
    for edit in edits:
        new_string = edit.get("new_string", "")
        if not new_string:
            continue

        old_string = edit.get("old_string", "")
        if not old_string:
            # File creation, full replace, or prepend — attribute from line 1
            line_count = new_string.count("\n") + 1
            positions.append({"start_line": 1, "end_line": line_count})
            continue

        rng = edit.get("range")
        if rng:
            positions.append({
                "start_line": rng.get("start_line_number", 1),
                "end_line": rng.get("end_line_number", 1),
            })
        elif file_content:
            idx = file_content.find(new_string)
            line_count = new_string.count("\n") + 1
            if idx != -1:
                start = file_content[:idx].count("\n") + 1
                positions.append({"start_line": start, "end_line": start + line_count - 1})
            else:
                positions.append({"start_line": 1, "end_line": line_count})
        else:
            line_count = new_string.count("\n") + 1
            positions.append({"start_line": 1, "end_line": line_count})
    return positions


# -------------------------------------------------------------------
# Trace construction
# -------------------------------------------------------------------

def create_trace(
    contributor_type: str,
    file_path: str,
    *,
    model: str | None = None,
    range_positions: list[dict] | None = None,
    range_contents: list[str] | None = None,
    transcript: str | None = None,
    metadata: dict | None = None,
    edit_sequence: int | None = None,
    anchor_path: str | None = None,
    resolution: ProjectResolution | None = None,
) -> dict | None:
    """Build a trace record dict, or None if the path is not traceable (no git and no detached config)."""
    res = resolution or resolve_file_project(file_path, anchor_path=anchor_path)
    if res is None:
        return None

    root = res.repo_root
    model_id = normalize_model_id(model)
    conversation_url = f"file://{transcript}" if transcript else None

    # Build ranges
    ranges: list[dict] = []
    if range_positions:
        for i, pos in enumerate(range_positions):
            r = {"start_line": pos["start_line"], "end_line": pos["end_line"]}
            if range_contents and i < len(range_contents) and range_contents[i]:
                r["content_hash"] = compute_content_hash(range_contents[i])
                r["line_hashes"] = compute_line_hashes(range_contents[i])
            ranges.append(r)
    if not ranges:
        ranges = [{"start_line": 1, "end_line": 1}]

    # Conversation entry
    conversation: dict = {
        "contributor": {"type": contributor_type},
        "ranges": ranges,
    }
    if model_id:
        conversation["contributor"]["model_id"] = model_id
    if conversation_url:
        conversation["url"] = conversation_url

    trace: dict = {
        "version": "2.0",
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": get_tool_info(),
        "files": [
            {
                "path": res.rel_path,
                "conversations": [conversation],
            }
        ],
    }

    if res.vcs:
        trace["vcs"] = res.vcs

    meta: dict = {}
    if metadata:
        meta = {k: v for k, v in metadata.items() if v is not None}
    meta["project_id"] = res.project_id
    meta["repo_root"] = root
    if edit_sequence is not None:
        meta["edit_sequence"] = edit_sequence
    if meta:
        trace["metadata"] = meta

    return trace


def cli_resolve_project_root(project_arg: str | None, cwd: str | None = None) -> str:
    """Resolve ``--project`` (path or known project_id) or cwd to a git root directory."""
    from .registry import get_project_record

    base = os.path.abspath(cwd or os.getcwd())
    if project_arg:
        p = os.path.abspath(os.path.expanduser(project_arg.strip()))
        if os.path.isdir(p):
            gr = git_repo_root_for_path(p)
            if gr:
                return gr
        rec = get_project_record(project_arg)
        if rec:
            cr = rec.get("canonical_root")
            if cr and os.path.isdir(cr):
                return os.path.realpath(cr)
            roots = rec.get("known_roots") or []
            for r in roots:
                if r and os.path.isdir(r):
                    return os.path.realpath(r)
        raise SystemExit(
            f"agent-trace: cannot resolve --project {project_arg!r} to a directory",
        )

    gr = git_repo_root_for_path(base)
    if gr:
        return gr
    raise SystemExit(
        "agent-trace: not inside a git repository; pass --project <path|id>",
    )


def discover_ambiguous_repo_roots(cwd: str | None = None) -> list[str]:
    """If ``cwd`` is not in a git repo but contains multiple git checkouts, return their roots."""
    base = os.path.abspath(cwd or os.getcwd())
    if git_repo_root_for_path(base):
        return []
    found: list[str] = []
    try:
        for name in sorted(os.listdir(base)):
            sub = os.path.join(base, name)
            if not os.path.isdir(sub):
                continue
            g = git_repo_root_for_path(sub)
            if g and g not in found:
                found.append(g)
    except OSError:
        pass
    return found
