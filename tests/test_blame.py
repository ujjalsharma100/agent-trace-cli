"""Tests for deterministic ledger-only blame."""

from __future__ import annotations

import json
import unittest

from agent_trace import blame


class TestBlameDeterministic(unittest.TestCase):
    def test_merge_attributions_adjacent_same_kind(self) -> None:
        a = [
            {"start_line": 1, "end_line": 2, "kind": "AI", "trace_id": "t1", "source": "ledger"},
            {"start_line": 3, "end_line": 4, "kind": "AI", "trace_id": "t1", "source": "ledger"},
        ]
        m = blame._merge_attributions(a)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["start_line"], 1)
        self.assertEqual(m[0]["end_line"], 4)

    def test_merge_adjacent_no_attribution_combines(self) -> None:
        a = [
            {"start_line": 1, "end_line": 1, "kind": "NO_ATTRIBUTION", "trace_id": None, "source": None},
            {"start_line": 2, "end_line": 2, "kind": "NO_ATTRIBUTION", "trace_id": None, "source": None},
        ]
        m = blame._merge_attributions(a)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["end_line"], 2)

    def test_attribute_from_ledger_ai(self) -> None:
        sha = "a" * 40
        ledgers = {
            sha: {
                "files": {
                    "f.py": {
                        "line_attributions": [
                            {
                                "start_line": 1,
                                "end_line": 5,
                                "type": "ai",
                                "trace_id": "tid",
                                "model_id": "m",
                            },
                        ],
                    },
                },
            },
        }
        seg = [{
            "commit_sha": sha,
            "start_line": 1,
            "end_line": 5,
            "orig_start_line": 1,
            "orig_end_line": 5,
            "content_lines": ["x"] * 5,
        }]
        attr, rem = blame._attribute_from_ledger(seg, ledgers, "f.py", traces=None)
        self.assertEqual(len(rem), 0)
        self.assertEqual(len(attr), 1)
        self.assertEqual(attr[0]["kind"], "AI")
        self.assertEqual(attr[0]["trace_id"], "tid")

    def test_no_attribution_when_no_ledger(self) -> None:
        sha = "b" * 40
        seg = [{
            "commit_sha": sha,
            "start_line": 1,
            "end_line": 3,
            "orig_start_line": 1,
            "orig_end_line": 3,
            "content_lines": ["a", "b", "c"],
        }]
        out = blame._attribute_deterministic(seg, "x.py", {}, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "NO_ATTRIBUTION")

    def test_filter_no_attribution(self) -> None:
        attrs = [
            {"kind": "AI", "start_line": 1, "end_line": 1},
            {"kind": "NO_ATTRIBUTION", "start_line": 2, "end_line": 2},
        ]
        f = blame._filter_no_attribution(attrs, show_no_attribution=False)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["kind"], "AI")


if __name__ == "__main__":
    unittest.main()
