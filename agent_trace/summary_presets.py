"""
Built-in summary command presets.

These presets are wrappers around local CLI tools:
  - claude-summary  -> `claude -p "<prompt>"`
  - cursor-summary  -> `cursor agent -p "<prompt>" --trust`
  - ollama-summary  -> `ollama run <model> "<prompt>"`

The preset command executes as a separate `agent-trace` subprocess so the
existing summary pipeline can continue to treat summaries as "any command that
reads transcript on stdin and prints summary on stdout".
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

PRESET_ALIASES = ("claude-summary", "cursor-summary", "ollama-summary")
DEFAULT_OLLAMA_MODEL = "llama3.1:8b"


def _build_prompt(transcript_text: str) -> str:
    t = (transcript_text or "").strip()
    if not t:
        return ""
    return (
        "You are summarizing a coding-assistant conversation transcript.\n"
        "Return concise plain text using this exact structure:\n"
        "- Objective:\n"
        "- Key changes:\n"
        "- Files touched:\n"
        "- Risks/follow-ups:\n"
        "- Test status:\n\n"
        "Keep it factual and avoid markdown code fences.\n\n"
        "Transcript:\n"
        f"{t}\n"
    )


def list_summary_presets() -> list[dict[str, Any]]:
    return [
        {
            "alias": "claude-summary",
            "tool": "claude",
            "description": "Run `claude -p <prompt>` locally.",
            "needs_model": False,
        },
        {
            "alias": "cursor-summary",
            "tool": "cursor",
            "description": "Run `cursor agent -p <prompt> --trust` locally.",
            "needs_model": False,
        },
        {
            "alias": "ollama-summary",
            "tool": "ollama",
            "description": "Run `ollama run <model> <prompt>` locally.",
            "needs_model": True,
            "default_model": DEFAULT_OLLAMA_MODEL,
        },
    ]


def build_preset_command(alias: str, *, model: str | None = None) -> str:
    """Command string to store in `summary.command`."""
    alias = (alias or "").strip()
    if alias not in PRESET_ALIASES:
        raise ValueError(f"unknown summary preset: {alias}")
    if alias == "ollama-summary":
        m = (model or "").strip() or DEFAULT_OLLAMA_MODEL
        return f"agent-trace summary preset-run ollama-summary --model {m}"
    return f"agent-trace summary preset-run {alias}"


def run_summary_preset(alias: str, transcript_text: str, *, model: str | None = None) -> int:
    """Read transcript text and print summary text to stdout. Returns exit code."""
    alias = (alias or "").strip()
    prompt = _build_prompt(transcript_text)
    if not prompt:
        return 1

    if alias == "claude-summary":
        if not shutil.which("claude"):
            return 1
        cmd = ["claude", "-p", prompt]
    elif alias == "cursor-summary":
        if not shutil.which("cursor"):
            return 1
        cmd = ["cursor", "agent", "-p", prompt, "--trust"]
    elif alias == "ollama-summary":
        if not shutil.which("ollama"):
            return 1
        m = (model or "").strip() or DEFAULT_OLLAMA_MODEL
        cmd = ["ollama", "run", m, prompt]
    else:
        return 1

    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return 1
    if r.returncode != 0:
        return r.returncode
    out = (r.stdout or "").strip()
    if not out:
        return 1
    sys.stdout.write(out + "\n")
    return 0
