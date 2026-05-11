"""Property-based tests: schema-valid JSON ↔ :mod:`agent_trace.models` round-trips."""

from __future__ import annotations

import json
import unittest

from hypothesis import HealthCheck, given, settings, strategies as st

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None  # type: ignore[misc, assignment]

from agent_trace.models import CommitLink, Ledger, Trace, schemas_dir

_SCHEMA_SKIP = Draft202012Validator is None

_hypothesis_settings = settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.filter_too_much, HealthCheck.too_slow],
)


def _schema(name: str) -> dict:
    return json.loads((schemas_dir() / name).read_text())


def _validate(instance: dict, schema_name: str) -> None:
    if Draft202012Validator is None:
        raise unittest.SkipTest("jsonschema is not installed")
    Draft202012Validator(_schema(schema_name)).validate(instance)


def _ascii_text(min_size: int = 1, max_size: int = 80) -> st.SearchStrategy[str]:
    return st.text(
        alphabet=st.characters(codec="utf-8", categories=("Lu", "Ll", "Nd", "Pc", "Pd", "Po", "Zs")),
        min_size=min_size,
        max_size=max_size,
    )


def _sha256_field() -> st.SearchStrategy[str]:
    return st.builds(
        lambda h: f"sha256:{h}",
        st.text(alphabet="0123456789abcdef", min_size=64, max_size=64),
    )


@st.composite
def _range_obj(draw) -> dict:
    start = draw(st.integers(min_value=1, max_value=500))
    end = draw(st.integers(min_value=start, max_value=500))
    r: dict = {"start_line": start, "end_line": end}
    if draw(st.booleans()):
        r["content_hash"] = draw(_sha256_field())
    if draw(st.booleans()):
        n = draw(st.integers(min_value=1, max_value=4))
        r["line_hashes"] = [
            {
                "line_offset": draw(st.integers(min_value=0, max_value=200)),
                "hash": draw(_sha256_field()),
            }
            for _ in range(n)
        ]
    return r


@st.composite
def _conversation_obj(draw) -> dict:
    ctype = draw(st.sampled_from(["ai", "human", "tool"]))
    contrib: dict = {"type": ctype}
    if ctype == "ai" and draw(st.booleans()):
        contrib["model_id"] = draw(_ascii_text(max_size=60))
    n_ranges = draw(st.integers(min_value=1, max_value=4))
    ranges = [draw(_range_obj()) for _ in range(n_ranges)]
    conv = {"contributor": contrib, "ranges": ranges}
    if draw(st.booleans()):
        conv["id"] = draw(st.sampled_from(["a" * 64, "b" * 64]))
    if draw(st.booleans()):
        conv["content_sha256"] = draw(st.sampled_from(["c" * 64, "d" * 64]))
    return conv


@st.composite
def _file_entry_obj(draw) -> dict:
    path = draw(st.one_of(st.sampled_from(["src/a.py", "README.md", "x.rs"]), _ascii_text(min_size=1, max_size=40)))
    n = draw(st.integers(min_value=1, max_value=3))
    convs = [draw(_conversation_obj()) for _ in range(n)]
    return {"path": path, "conversations": convs}


@st.composite
def trace_record_dicts(draw) -> dict:
    n_files = draw(st.integers(min_value=1, max_value=4))
    files = [draw(_file_entry_obj()) for _ in range(n_files)]
    tool: dict = {"name": draw(_ascii_text(min_size=1, max_size=30))}
    if draw(st.booleans()):
        tool["version"] = draw(_ascii_text(min_size=1, max_size=20))
    out: dict = {
        "version": "2.0",
        "id": draw(_ascii_text(min_size=1, max_size=48)),
        "timestamp": draw(
            st.sampled_from(
                ["2026-05-09T12:00:00Z", "2026-05-09T12:00:00.000Z", "2026-05-09T12:00:00+00:00"],
            ),
        ),
        "tool": tool,
        "files": files,
    }
    if draw(st.booleans()):
        out["vcs"] = {"type": "git", "revision": draw(_ascii_text(min_size=1, max_size=40))}
    if draw(st.booleans()):
        meta: dict = {}
        if draw(st.booleans()):
            meta["session_id"] = draw(_ascii_text(min_size=1, max_size=32))
        if draw(st.booleans()):
            meta["edit_sequence"] = draw(st.integers(min_value=0, max_value=10_000))
        if draw(st.booleans()):
            meta["is_creation"] = draw(st.booleans())
        out["metadata"] = meta if meta else {"tool_name": "x"}
    return out


@st.composite
def _line_evidence(draw) -> dict:
    return {
        "line": draw(st.integers(min_value=1, max_value=300)),
        "hash": draw(_ascii_text(min_size=4, max_size=24)),
        "content": draw(_ascii_text(min_size=0, max_size=40)),
    }


@st.composite
def _line_segment(draw) -> dict:
    start = draw(st.integers(min_value=1, max_value=200))
    end = draw(st.integers(min_value=start, max_value=250))
    seg: dict = {
        "start_line": start,
        "end_line": end,
        "type": "ai",
        "trace_id": draw(_ascii_text(min_size=1, max_size=36)),
    }
    if draw(st.booleans()):
        seg["model_id"] = draw(st.one_of(st.none(), _ascii_text(min_size=1, max_size=40)))
    if draw(st.booleans()):
        seg["conversation_id"] = draw(st.one_of(st.none(), st.just("a" * 64)))
    if draw(st.booleans()):
        n = draw(st.integers(min_value=0, max_value=5))
        if n:
            seg["evidence"] = [draw(_line_evidence()) for _ in range(n)]
    return seg


@st.composite
def _file_ledger(draw) -> dict:
    n = draw(st.integers(min_value=0, max_value=6))
    segs = [draw(_line_segment()) for _ in range(n)]
    return {"line_attributions": segs}


@st.composite
def ledger_dicts(draw) -> dict:
    n_paths = draw(st.integers(min_value=0, max_value=4))
    paths = draw(
        st.lists(_ascii_text(min_size=1, max_size=24), min_size=n_paths, max_size=n_paths, unique=True),
    )
    files = {p: draw(_file_ledger()) for p in paths}
    out: dict = {
        "version": "2.0",
        "commit_sha": draw(_ascii_text(min_size=1, max_size=40)),
        "parent_sha": draw(st.one_of(st.none(), _ascii_text(min_size=1, max_size=40))),
        "committed_at": draw(st.one_of(st.none(), st.just("2026-05-09T12:00:00+00:00"))),
        "created_at": "2026-05-09T12:00:01+00:00",
        "trace_ids": draw(st.lists(_ascii_text(min_size=1, max_size=20), max_size=8)),
        "files": files,
    }
    if draw(st.booleans()):
        out["parent_committed_at"] = draw(st.one_of(st.none(), st.just("2026-05-08T12:00:00+00:00")))
    if draw(st.booleans()):
        kind = draw(st.sampled_from(["cherry-pick", "revert", "merge"]))
        df: dict = {"kind": kind}
        if draw(st.booleans()):
            df["source_sha"] = draw(_ascii_text(min_size=6, max_size=40))
        if draw(st.booleans()):
            df["parents"] = draw(st.lists(_ascii_text(min_size=4, max_size=12), max_size=3))
        out["derived_from"] = df
    if draw(st.booleans()):
        out["used_fallback"] = draw(st.booleans())
    return out


@st.composite
def commit_link_dicts(draw) -> dict:
    out: dict = {
        "commit_sha": draw(_ascii_text(min_size=1, max_size=40)),
        "parent_sha": draw(st.one_of(st.none(), _ascii_text(min_size=1, max_size=40))),
        "trace_ids": draw(st.lists(_ascii_text(min_size=1, max_size=16), max_size=6)),
        "files_changed": draw(st.lists(_ascii_text(min_size=1, max_size=30), max_size=5)),
        "committed_at": draw(st.one_of(st.none(), st.just("2026-05-09T11:59:00+00:00"))),
        "created_at": "2026-05-09T12:00:05+00:00",
    }
    if draw(st.booleans()):
        leg = draw(ledger_dicts())
        out["ledger"] = leg
    return out


@unittest.skipIf(_SCHEMA_SKIP, "jsonschema not installed")
class TestTraceRecordHypothesis(unittest.TestCase):
    @given(trace_record_dicts())
    @_hypothesis_settings
    def test_trace_roundtrip_schema_idempotent(self, raw: dict) -> None:
        _validate(raw, "trace-record.schema.json")
        m = Trace.from_dict(raw)
        d1 = m.to_dict()
        _validate(d1, "trace-record.schema.json")
        self.assertEqual(Trace.from_dict(d1).to_dict(), d1)


@unittest.skipIf(_SCHEMA_SKIP, "jsonschema not installed")
class TestLedgerHypothesis(unittest.TestCase):
    @given(ledger_dicts())
    @_hypothesis_settings
    def test_ledger_roundtrip_schema_idempotent(self, raw: dict) -> None:
        _validate(raw, "ledger.schema.json")
        m = Ledger.from_dict(raw)
        d1 = m.to_dict()
        _validate(d1, "ledger.schema.json")
        self.assertEqual(Ledger.from_dict(d1).to_dict(), d1)


@unittest.skipIf(_SCHEMA_SKIP, "jsonschema not installed")
class TestCommitLinkHypothesis(unittest.TestCase):
    @given(commit_link_dicts())
    @_hypothesis_settings
    def test_commit_link_roundtrip_schema_idempotent(self, raw: dict) -> None:
        _validate(raw, "commit-link.schema.json")
        m = CommitLink.from_dict(raw)
        d1 = m.to_dict()
        _validate(d1, "commit-link.schema.json")
        self.assertEqual(CommitLink.from_dict(d1).to_dict(), d1)


if __name__ == "__main__":
    unittest.main()
