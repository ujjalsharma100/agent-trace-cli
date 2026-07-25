"""Package version is defined in one place."""

from __future__ import annotations

from agent_trace import __version__
from agent_trace.cli import VERSION


def test_cli_version_matches_package_version() -> None:
    assert VERSION == __version__
