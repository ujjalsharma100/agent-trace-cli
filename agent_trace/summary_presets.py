"""
Built-in summary command presets.

These presets are wrappers around local CLI tools:
  - claude-summary  -> ``claude -p "<prompt>"`` (prompt in argv; huge transcripts may hit OS limits)
  - cursor-summary  -> ``cursor agent --print --trust`` with the prompt on **stdin** (avoids argv limits)
  - ollama-summary  -> ``ollama run <model> --think=false`` with the prompt on **stdin**

The preset command executes as a separate `agent-trace` subprocess so the
existing summary pipeline can continue to treat summaries as "any command that
reads transcript on stdin and prints summary on stdout".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
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


def augment_path_env() -> dict[str, str]:
    """Return a copy of the environment with common binary dirs prepended to ``PATH``.

    Cursor / GUI hook processes often get a minimal ``PATH``, so ``ollama`` and
    ``cursor`` are not found even when installed (e.g. Homebrew under
    ``/opt/homebrew/bin``).
    """
    env = dict(os.environ)
    extra_dirs = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
    ]
    prefix = os.pathsep.join(d for d in extra_dirs if Path(d).is_dir())
    if not prefix:
        return env
    current = env.get("PATH", "")
    env["PATH"] = prefix + os.pathsep + current if current else prefix
    return env


def _which(name: str) -> str | None:
    return shutil.which(name, path=augment_path_env().get("PATH"))


def _emit_preset_error(tool: str, r: subprocess.CompletedProcess) -> None:
    err = (r.stderr or "").strip()
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        detail = err or out or f"exit {r.returncode}"
    else:
        detail = err or "empty stdout"
    print(f"agent-trace: {tool}: {detail[:1200]}", file=sys.stderr)


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
            "description": "Run `cursor agent --print --trust` (prompt on stdin; optional --workspace from hook env).",
            "needs_model": False,
        },
        {
            "alias": "ollama-summary",
            "tool": "ollama",
            "description": "Run `ollama run <model> --think=false` with the prompt on stdin.",
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

    env = augment_path_env()

    if alias == "claude-summary":
        exe = _which("claude")
        if not exe:
            print("agent-trace: claude-summary: `claude` not found on PATH", file=sys.stderr)
            return 1
        cmd = [exe, "-p", prompt]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        except OSError:
            return 1
    elif alias == "cursor-summary":
        exe = _which("cursor")
        if not exe:
            print("agent-trace: cursor-summary: `cursor` not found on PATH", file=sys.stderr)
            return 1
        # Do not pass the transcript as a CLI argument: ``-p`` is ``--print``, so
        # ``cursor agent -p <transcript>`` puts the whole file in argv and fails with
        # "argument list too long" for real sessions. Pipe the built prompt on stdin instead.
        cmd = [exe, "agent", "--print", "--trust"]
        ws = env.get("CURSOR_PROJECT_DIR") or env.get("CLAUDE_PROJECT_DIR")
        if isinstance(ws, str) and ws.strip():
            cmd.extend(["--workspace", ws.strip()])
        try:
            r = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                env=env,
            )
        except OSError:
            return 1
    elif alias == "ollama-summary":
        exe = _which("ollama")
        if not exe:
            print("agent-trace: ollama-summary: `ollama` not found on PATH", file=sys.stderr)
            return 1
        m = (model or "").strip() or DEFAULT_OLLAMA_MODEL
        # Pass the prompt on stdin so large transcripts do not hit OS "argument list too long"
        # (``ollama run MODEL PROMPT`` embeds the full transcript in argv).
        # ``--think=false`` avoids qwen and similar models flooding stdout with thinking traces.
        cmd = [exe, "run", m, "--think=false"]
        try:
            r = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                env=env,
            )
        except OSError as e:
            print(f"agent-trace: ollama-summary: {e}", file=sys.stderr)
            return 1
    else:
        return 1

    if r.returncode != 0:
        _emit_preset_error(f"{alias}", r)
        return r.returncode
    out = (r.stdout or "").strip()
    if not out:
        _emit_preset_error(f"{alias}", r)
        return 1
    sys.stdout.write(out + "\n")
    return 0
