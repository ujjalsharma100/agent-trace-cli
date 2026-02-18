"""
Post-rewrite ledger remapping — updates ledger commit SHAs after rebase/amend.

Git's ``post-rewrite`` hook provides ``old_sha new_sha`` lines on stdin.
This module reads those mappings and updates ``.agent-trace/ledgers.jsonl``
so that ledgers remain keyed to the correct (new) commit SHAs.

No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def rewrite_ledgers(project_dir: str | None = None) -> int:
    """After rebase/amend, remap old commit SHAs to new ones in ledgers.

    Reads old→new SHA mapping from stdin (git provides this in post-rewrite).
    Updates ``ledgers.jsonl`` entries. Returns count of remapped ledgers.
    """
    if project_dir is None:
        import os
        project_dir = os.getcwd()

    # Read old→new SHA mappings from stdin
    sha_map: dict[str, str] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            old_sha = parts[0]
            new_sha = parts[1]
            sha_map[old_sha] = new_sha

    if not sha_map:
        return 0

    ledgers_path = Path(project_dir) / ".agent-trace" / "ledgers.jsonl"
    if not ledgers_path.exists():
        return 0

    # Read all ledgers
    ledgers: list[dict] = []
    try:
        for line in ledgers_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    ledgers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return 0

    # Remap SHAs
    remapped = 0
    for ledger in ledgers:
        old_sha = ledger.get("commit_sha", "")
        if old_sha in sha_map:
            ledger["commit_sha"] = sha_map[old_sha]
            remapped += 1
        # Also remap parent_sha if it was rewritten
        old_parent = ledger.get("parent_sha", "")
        if old_parent and old_parent in sha_map:
            ledger["parent_sha"] = sha_map[old_parent]

    if remapped == 0:
        return 0

    # Write back
    try:
        with open(ledgers_path, "w") as f:
            for ledger in ledgers:
                f.write(json.dumps(ledger) + "\n")
    except OSError:
        return 0

    return remapped
