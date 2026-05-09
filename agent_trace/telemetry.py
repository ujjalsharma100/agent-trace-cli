"""
Opt-in anonymous CLI telemetry (stdlib only).

No third-party services in M0: the collect URL points at an invalid endpoint until a
backend exists; failures are always swallowed so tracing never affects user commands.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .config import get_global_config, save_global_config

# Placeholder until a real collector ships (see docs/concepts/telemetry.md).
TELEMETRY_COLLECT_URL = "http://127.0.0.1:0/"

_ENV = "AGENT_TRACE_TELEMETRY"


def _parse_env_override(raw: str | None) -> bool | None:
    """Return True/False if env forces telemetry on/off; None if unset or unknown."""
    if raw is None or not str(raw).strip():
        return None
    v = str(raw).strip().lower()
    if v in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    if v in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    return None


def telemetry_env_override() -> bool | None:
    return _parse_env_override(os.environ.get(_ENV))


def telemetry_config_enabled() -> bool:
    cfg = get_global_config()
    tel = cfg.get("telemetry")
    if not isinstance(tel, dict):
        return False
    return bool(tel.get("enabled"))


def telemetry_effective_enabled() -> bool:
    env = telemetry_env_override()
    if env is not None:
        return env
    return telemetry_config_enabled()


def ensure_install_id(cfg: dict | None = None) -> str:
    """Return persistent anonymous install id; creates one under global config if missing.

    Pass ``cfg`` when mutating the same in-memory dict as another helper (e.g.
    `set_telemetry_enabled`) so a reload from disk does not drop unsaved fields.
    """
    cfg = get_global_config() if cfg is None else cfg
    tel = cfg.get("telemetry")
    if not isinstance(tel, dict):
        tel = {}
        cfg["telemetry"] = tel
    iid = tel.get("install_id")
    if isinstance(iid, str) and len(iid) >= 8:
        return iid
    tel["install_id"] = str(uuid.uuid4())
    save_global_config(cfg)
    return tel["install_id"]


def set_telemetry_enabled(enabled: bool) -> None:
    cfg = get_global_config()
    tel = cfg.setdefault("telemetry", {})
    if not isinstance(tel, dict):
        tel = {}
        cfg["telemetry"] = tel
    tel["enabled"] = bool(enabled)
    if enabled:
        ensure_install_id(cfg)
    save_global_config(cfg)


def telemetry_status_lines() -> list[str]:
    """Human-readable lines for `agent-trace --telemetry status`."""
    cfg_on = telemetry_config_enabled()
    env_raw = os.environ.get(_ENV)
    env = telemetry_env_override()
    eff = telemetry_effective_enabled()
    lines = [
        "Telemetry (anonymous CLI usage metrics)",
        f"  Config file preference: {'on' if cfg_on else 'off'}",
        f"  {_ENV}: {repr(env_raw) if env_raw is not None else '(not set)'}",
    ]
    if env is None and env_raw is not None and str(env_raw).strip():
        lines.append("  (environment value is not a recognised boolean — ignored)")
    lines.append(f"  Effective (what the CLI uses): {'on' if eff else 'off'}")
    lines.append("")
    lines.append(
        "When on, each command may POST anonymous JSON: "
        "install_id, version, command, exit_code, duration_ms. "
        "See docs/concepts/telemetry.md.",
    )
    return lines


def maybe_report_cli_run(
    *,
    version: str,
    command: str,
    exit_code: int,
    duration_ms: int,
) -> None:
    """Fire-and-forget telemetry; never raises."""
    try:
        if not telemetry_effective_enabled():
            return
        install_id = ensure_install_id()
        payload: dict[str, Any] = {
            "install_id": install_id,
            "version": version,
            "command": command,
            "exit_code": int(exit_code),
            "duration_ms": int(duration_ms),
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            TELEMETRY_COLLECT_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TypeError):
        pass
    except socket.timeout:
        pass

