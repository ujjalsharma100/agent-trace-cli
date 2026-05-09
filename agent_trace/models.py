"""
Typed models mirroring JSON Schemas in ``agent_trace/schemas/`` — trace records, ledgers,
commit links, git notes, remotes, and sync state.

Use :func:`from_dict` dispatchers and ``.to_dict()`` for JSONL round-trips.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast


def schemas_dir() -> Path:
    """Directory containing ``*.schema.json`` shipped inside the ``agent_trace`` package."""
    return Path(__file__).resolve().parent / "schemas"


# --- Trace record (traces.jsonl) ---


@dataclass
class LineHash:
    line_offset: int
    hash: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineHash:
        return cls(
            line_offset=int(d["line_offset"]),
            hash=str(d["hash"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_offset": self.line_offset,
            "hash": self.hash,
        }


@dataclass
class Range:
    start_line: int
    end_line: int
    content_hash: str | None = None
    line_hashes: list[LineHash] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Range:
        lhs = d.get("line_hashes")
        line_hashes: list[LineHash] | None = None
        if isinstance(lhs, list):
            line_hashes = [LineHash.from_dict(cast(dict[str, Any], x)) for x in lhs if isinstance(x, dict)]
        return cls(
            start_line=int(d["start_line"]),
            end_line=int(d["end_line"]),
            content_hash=d.get("content_hash"),
            line_hashes=line_hashes,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.content_hash is not None:
            d["content_hash"] = self.content_hash
        if self.line_hashes is not None:
            d["line_hashes"] = [lh.to_dict() for lh in self.line_hashes]
        return d


@dataclass
class Contributor:
    type: str
    model_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Contributor:
        return cls(type=str(d["type"]), model_id=d.get("model_id"))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.model_id is not None:
            out["model_id"] = self.model_id
        return out


@dataclass
class Conversation:
    contributor: Contributor
    ranges: list[Range]
    url: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Conversation:
        ranges = [Range.from_dict(cast(dict[str, Any], x)) for x in d.get("ranges", []) if isinstance(x, dict)]
        return cls(
            contributor=Contributor.from_dict(cast(dict[str, Any], d["contributor"])),
            ranges=ranges,
            url=d.get("url"),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contributor": self.contributor.to_dict(),
            "ranges": [r.to_dict() for r in self.ranges],
        }
        if self.url is not None:
            out["url"] = self.url
        return out


@dataclass
class FileEntry:
    path: str
    conversations: list[Conversation]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileEntry:
        convs = [
            Conversation.from_dict(cast(dict[str, Any], x))
            for x in d.get("conversations", [])
            if isinstance(x, dict)
        ]
        return cls(path=str(d["path"]), conversations=convs)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "conversations": [c.to_dict() for c in self.conversations]}


@dataclass
class Trace:
    """One trace record (``traces.jsonl`` line)."""

    version: str
    id: str
    timestamp: str
    tool: dict[str, Any]
    files: list[FileEntry]
    vcs: dict[str, str] | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Trace:
        file_entries = [
            FileEntry.from_dict(cast(dict[str, Any], x)) for x in d.get("files", []) if isinstance(x, dict)
        ]
        vcs = d.get("vcs")
        meta = d.get("metadata")
        return cls(
            version=str(d["version"]),
            id=str(d["id"]),
            timestamp=str(d["timestamp"]),
            tool=dict(d["tool"]) if isinstance(d.get("tool"), dict) else {},
            files=file_entries,
            vcs=dict(vcs) if isinstance(vcs, dict) else None,
            metadata=dict(meta) if isinstance(meta, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "id": self.id,
            "timestamp": self.timestamp,
            "tool": self.tool,
            "files": [f.to_dict() for f in self.files],
        }
        if self.vcs is not None:
            out["vcs"] = self.vcs
        if self.metadata is not None:
            out["metadata"] = self.metadata
        return out


# --- Ledger (ledgers.jsonl) ---


@dataclass
class LineEvidence:
    """Per-line audit trail: hash and content of each AI-attributed line."""

    line: int
    hash: str
    content: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineEvidence:
        return cls(
            line=int(d["line"]),
            hash=str(d["hash"]),
            content=str(d.get("content", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "hash": self.hash, "content": self.content}


@dataclass
class LineSegment:
    """An AI-attributed run of lines. Schema 2.0: only AI segments exist;
    anything not in a segment is implicitly NO_ATTRIBUTION."""

    start_line: int
    end_line: int
    trace_id: str
    type: str = "ai"
    model_id: str | None = None
    conversation_url: str | None = None
    evidence: list[LineEvidence] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineSegment:
        ev_raw = d.get("evidence")
        evidence: list[LineEvidence] | None = None
        if isinstance(ev_raw, list):
            evidence = [
                LineEvidence.from_dict(cast(dict[str, Any], x))
                for x in ev_raw
                if isinstance(x, dict)
            ]
        return cls(
            start_line=int(d["start_line"]),
            end_line=int(d["end_line"]),
            type=str(d.get("type", "ai")),
            trace_id=str(d["trace_id"]),
            model_id=d.get("model_id"),
            conversation_url=d.get("conversation_url"),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "type": self.type,
            "trace_id": self.trace_id,
        }
        if self.model_id is not None:
            out["model_id"] = self.model_id
        if self.conversation_url is not None:
            out["conversation_url"] = self.conversation_url
        if self.evidence is not None:
            out["evidence"] = [e.to_dict() for e in self.evidence]
        return out


@dataclass
class FileLedger:
    line_attributions: list[LineSegment]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileLedger:
        segs = [
            LineSegment.from_dict(cast(dict[str, Any], x))
            for x in d.get("line_attributions", [])
            if isinstance(x, dict)
        ]
        return cls(line_attributions=segs)

    def to_dict(self) -> dict[str, Any]:
        return {"line_attributions": [s.to_dict() for s in self.line_attributions]}


@dataclass
class Ledger:
    version: str
    commit_sha: str
    parent_sha: str | None
    committed_at: str | None
    created_at: str
    trace_ids: list[str]
    files: dict[str, FileLedger]
    parent_committed_at: str | None = None
    derived_from: dict[str, Any] | None = None
    used_fallback: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ledger:
        raw_files = d.get("files", {})
        files: dict[str, FileLedger] = {}
        if isinstance(raw_files, dict):
            for path, fv in raw_files.items():
                if isinstance(fv, dict):
                    files[str(path)] = FileLedger.from_dict(fv)
        df = d.get("derived_from")
        return cls(
            version=str(d["version"]),
            commit_sha=str(d["commit_sha"]),
            parent_sha=d.get("parent_sha"),
            parent_committed_at=d.get("parent_committed_at"),
            committed_at=d.get("committed_at"),
            created_at=str(d["created_at"]),
            trace_ids=[str(x) for x in d.get("trace_ids", [])],
            files=files,
            derived_from=df if isinstance(df, dict) else None,
            used_fallback=bool(d.get("used_fallback", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "commit_sha": self.commit_sha,
            "parent_sha": self.parent_sha,
            "committed_at": self.committed_at,
            "created_at": self.created_at,
            "trace_ids": list(self.trace_ids),
            "files": {k: v.to_dict() for k, v in self.files.items()},
        }
        if self.parent_committed_at is not None:
            out["parent_committed_at"] = self.parent_committed_at
        if self.derived_from is not None:
            out["derived_from"] = dict(self.derived_from)
        if self.used_fallback:
            out["used_fallback"] = True
        return out


# --- Commit link (commit-links.jsonl) ---


@dataclass
class CommitLink:
    commit_sha: str
    parent_sha: str | None
    trace_ids: list[str]
    files_changed: list[str]
    committed_at: str | None
    created_at: str
    ledger: Ledger | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CommitLink:
        led = d.get("ledger")
        ledger_obj: Ledger | None = None
        if isinstance(led, dict):
            ledger_obj = Ledger.from_dict(led)
        return cls(
            commit_sha=str(d["commit_sha"]),
            parent_sha=d.get("parent_sha"),
            trace_ids=[str(x) for x in d.get("trace_ids", [])],
            files_changed=[str(x) for x in d.get("files_changed", [])],
            committed_at=d.get("committed_at"),
            created_at=str(d["created_at"]),
            ledger=ledger_obj,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "commit_sha": self.commit_sha,
            "parent_sha": self.parent_sha,
            "trace_ids": list(self.trace_ids),
            "files_changed": list(self.files_changed),
            "committed_at": self.committed_at,
            "created_at": self.created_at,
        }
        if self.ledger is not None:
            out["ledger"] = self.ledger.to_dict()
        return out


# --- Git note (refs/notes/agent-trace) ---


@dataclass
class GitNoteStats:
    """Schema 2.0: only AI-line counts are tracked. Everything else is
    implicitly NO_ATTRIBUTION. ``total_changed_lines`` is optional context."""

    ai_lines: int
    total_changed_lines: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GitNoteStats:
        total = d.get("total_changed_lines")
        return cls(
            ai_lines=int(d["ai_lines"]),
            total_changed_lines=int(total) if total is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ai_lines": self.ai_lines}
        if self.total_changed_lines is not None:
            out["total_changed_lines"] = self.total_changed_lines
        return out


@dataclass
class GitNoteLedgerSection:
    files: dict[str, FileLedger]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GitNoteLedgerSection:
        raw = d.get("files", {})
        files: dict[str, FileLedger] = {}
        if isinstance(raw, dict):
            for path, fv in raw.items():
                if isinstance(fv, dict):
                    files[str(path)] = FileLedger.from_dict(fv)
        return cls(files=files)

    def to_dict(self) -> dict[str, Any]:
        return {"files": {k: v.to_dict() for k, v in self.files.items()}}


@dataclass
class GitNote:
    version: str
    trace_ids: list[str]
    ledger_hash: str
    stats: GitNoteStats
    ledger: GitNoteLedgerSection | None = None
    summary: dict[str, str] | None = None
    prompts: list[str] | None = None
    all_session_conversations: list[dict[str, Any]] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GitNote:
        ledger_sec: GitNoteLedgerSection | None = None
        if isinstance(d.get("ledger"), dict):
            ledger_sec = GitNoteLedgerSection.from_dict(cast(dict[str, Any], d["ledger"]))
        summary = d.get("summary")
        prompts = d.get("prompts")
        pr: list[str] | None = None
        if isinstance(prompts, list):
            pr = [str(x) for x in prompts]
        asc = d.get("all_session_conversations")
        asc_list: list[dict[str, Any]] | None = None
        if isinstance(asc, list):
            asc_list = [cast(dict[str, Any], x) for x in asc if isinstance(x, dict)]
        return cls(
            version=str(d["version"]),
            trace_ids=[str(x) for x in d.get("trace_ids", [])],
            ledger_hash=str(d["ledger_hash"]),
            stats=GitNoteStats.from_dict(cast(dict[str, Any], d["stats"])),
            ledger=ledger_sec,
            summary=dict(summary) if isinstance(summary, dict) else None,
            prompts=pr,
            all_session_conversations=asc_list,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": self.version,
            "trace_ids": list(self.trace_ids),
            "ledger_hash": self.ledger_hash,
            "stats": self.stats.to_dict(),
        }
        if self.ledger is not None:
            out["ledger"] = self.ledger.to_dict()
        if self.summary is not None:
            out["summary"] = dict(self.summary)
        if self.prompts is not None:
            out["prompts"] = list(self.prompts)
        if self.all_session_conversations is not None:
            out["all_session_conversations"] = [dict(x) for x in self.all_session_conversations]
        return out


# --- Remotes (~/.agent-trace/projects/<id>/remotes.json) ---


@dataclass
class AuthConfig:
    type: str
    token_ref: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuthConfig:
        return cls(type=str(d["type"]), token_ref=str(d["token_ref"]))

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "token_ref": self.token_ref}


@dataclass
class Remote:
    url: str
    auth: AuthConfig | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Remote:
        auth = d.get("auth")
        auth_obj: AuthConfig | None = None
        if isinstance(auth, dict):
            auth_obj = AuthConfig.from_dict(auth)
        return cls(url=str(d["url"]), auth=auth_obj)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url}
        if self.auth is not None:
            out["auth"] = self.auth.to_dict()
        return out


@dataclass
class RemotesConfig:
    """Root object of ``remotes.json``: remote name → config."""

    remotes: dict[str, Remote] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RemotesConfig:
        remotes: dict[str, Remote] = {}
        for name, rv in d.items():
            if isinstance(rv, dict):
                remotes[str(name)] = Remote.from_dict(rv)
        return cls(remotes=remotes)

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() for k, v in self.remotes.items()}


# --- Sync state (~/.agent-trace/projects/<id>/sync-state.json) ---


@dataclass
class LastPushCursors:
    traces_max_timestamp: str | None = None
    ledgers_max_commit_at: str | None = None
    commit_links_max_commit_at: str | None = None
    conversations_max_updated_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LastPushCursors:
        return cls(
            traces_max_timestamp=d.get("traces_max_timestamp"),
            ledgers_max_commit_at=d.get("ledgers_max_commit_at"),
            commit_links_max_commit_at=d.get("commit_links_max_commit_at"),
            conversations_max_updated_at=d.get("conversations_max_updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        r = asdict(self)
        return {k: v for k, v in r.items() if v is not None}


@dataclass
class LastPullInfo:
    at: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LastPullInfo:
        return cls(at=str(d["at"]))

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at}


@dataclass
class RemoteSyncState:
    last_push: LastPushCursors | None = None
    last_pull: LastPullInfo | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RemoteSyncState:
        lp = d.get("last_push")
        lpl = d.get("last_pull")
        return cls(
            last_push=LastPushCursors.from_dict(cast(dict[str, Any], lp)) if isinstance(lp, dict) else None,
            last_pull=LastPullInfo.from_dict(cast(dict[str, Any], lpl)) if isinstance(lpl, dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.last_push is not None:
            lp = self.last_push.to_dict()
            if lp:
                out["last_push"] = lp
        if self.last_pull is not None:
            out["last_pull"] = self.last_pull.to_dict()
        return out


@dataclass
class SyncState:
    remotes: dict[str, RemoteSyncState]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SyncState:
        raw = d.get("remotes", {})
        remotes: dict[str, RemoteSyncState] = {}
        if isinstance(raw, dict):
            for name, rv in raw.items():
                if isinstance(rv, dict):
                    remotes[str(name)] = RemoteSyncState.from_dict(rv)
        return cls(remotes=remotes)

    def to_dict(self) -> dict[str, Any]:
        return {"remotes": {k: v.to_dict() for k, v in self.remotes.items()}}


def trace_from_dict(d: dict[str, Any]) -> Trace:
    return Trace.from_dict(d)


def ledger_from_dict(d: dict[str, Any]) -> Ledger:
    return Ledger.from_dict(d)
