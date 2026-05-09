"""Tests for opt-in CLI telemetry."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from agent_trace import telemetry as tel


def test_parse_env_override():
    assert tel._parse_env_override(None) is None
    assert tel._parse_env_override("") is None
    assert tel._parse_env_override("0") is False
    assert tel._parse_env_override("off") is False
    assert tel._parse_env_override("1") is True
    assert tel._parse_env_override("yes") is True
    assert tel._parse_env_override("maybe") is None


def test_effective_enabled_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telemetry": {"enabled": True, "install_id": "x" * 36}}))

    monkeypatch.setenv("AGENT_TRACE_TELEMETRY", "0")
    assert tel.telemetry_effective_enabled() is False

    monkeypatch.setenv("AGENT_TRACE_TELEMETRY", "1")
    assert tel.telemetry_effective_enabled() is True


def test_maybe_report_cli_run_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telemetry": {"enabled": True}}))

    def boom(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(tel.urllib.request, "urlopen", boom)
    tel.maybe_report_cli_run(
        version="0.0.0",
        command="blame",
        exit_code=0,
        duration_ms=12,
    )


def test_maybe_report_skips_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    spy = mock.Mock()
    monkeypatch.setattr(tel.urllib.request, "urlopen", spy)
    tel.maybe_report_cli_run(version="0.1.0", command="status", exit_code=0, duration_ms=1)
    spy.assert_not_called()


def test_set_telemetry_creates_install_id(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    tel.set_telemetry_enabled(True)
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["telemetry"]["enabled"] is True
    assert len(cfg["telemetry"]["install_id"]) == 36


def test_main_telemetry_status_runs(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    from agent_trace.cli import main

    with mock.patch("agent_trace.cli.telemetry_status_lines", return_value=["line1", "line2"]):
        with mock.patch("sys.argv", ["agent-trace", "--telemetry", "status"]):
            main()
    out = capsys.readouterr().out
    assert "line1" in out


def test_main_rejects_telemetry_with_subcommand(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    from agent_trace.cli import main

    with pytest.raises(SystemExit) as ei:
        with mock.patch("sys.argv", ["agent-trace", "--telemetry", "on", "init"]):
            main()
    assert ei.value.code != 0


def test_main_reports_after_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AGENT_TRACE_HOME", str(tmp_path))
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telemetry": {"enabled": True, "install_id": str(__import__("uuid").uuid4())}}))

    spy = mock.Mock()
    monkeypatch.setattr("agent_trace.cli.maybe_report_cli_run", spy)

    from agent_trace.cli import main

    with mock.patch("sys.argv", ["agent-trace", "--version"]):
        with pytest.raises(SystemExit):
            main()
    spy.assert_not_called()

    with mock.patch("sys.argv", ["agent-trace", "projects"]):
        main()
    spy.assert_called_once()
    kw = spy.call_args.kwargs
    assert kw["command"] == "projects"
    assert kw["exit_code"] == 0
