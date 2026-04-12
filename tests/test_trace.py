"""Tests for trace construction helpers (Phase 1a)."""

from __future__ import annotations

import unittest

from agent_trace.trace import compute_range_positions


class TestComputeRangePositions(unittest.TestCase):
    def test_empty_old_string_full_new_without_file(self) -> None:
        edits = [{"old_string": "", "new_string": "line1\nline2\n"}]
        pos = compute_range_positions(edits, None)
        # Trailing newline yields an extra logical line (line_count = count(\n) + 1)
        self.assertEqual(pos, [{"start_line": 1, "end_line": 3}])

    def test_empty_old_string_ignores_file_content_find(self) -> None:
        """Creation-style edit: do not use substring search when old_string is empty."""
        edits = [{"old_string": "", "new_string": "a\nb\n"}]
        pos = compute_range_positions(edits, "zzz")
        self.assertEqual(pos, [{"start_line": 1, "end_line": 3}])

    def test_explicit_range_metadata(self) -> None:
        edits = [{
            "old_string": "x",
            "new_string": "y",
            "range": {"start_line_number": 3, "end_line_number": 5},
        }]
        pos = compute_range_positions(edits, "ignored")
        self.assertEqual(pos[0]["start_line"], 3)
        self.assertEqual(pos[0]["end_line"], 5)


if __name__ == "__main__":
    unittest.main()
