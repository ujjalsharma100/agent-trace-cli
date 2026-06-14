# Contributing to agent-trace

Thanks for your interest in improving **agent-trace**. This guide covers the
contributor workflow — local setup, tests, and project conventions — and then
goes deep on the most common extension: **adding support for a new coding-agent
harness** (Cursor, Claude Code, Codex CLI, … and whatever comes next).

If you only want to *use* the tool, see the [user documentation](docs/index.md)
instead.

---

## Project philosophy

A few principles shape almost every design decision. Please keep them in mind
when proposing changes:

- **Stdlib only.** The runtime package (`agent_trace/`) has **no third-party
  dependencies** and targets **Python 3.9+**. Test/build tooling may use
  packages, but nothing the CLI imports at runtime should.
- **Local-first.** Hooks write JSONL under `AGENT_TRACE_HOME`; nothing touches
  the network during normal editing. Sync is always explicit
  (`push` / `pull` / `sync`).
- **Deterministic attribution.** `blame` is **ledger-only** and **binary** —
  a line is either `AI` (matching trace evidence) or `No attribution`. We never
  guess, and we never claim a line was human-written.
- **Never crash the agent.** `agent-trace record` runs inside your coding
  agent's hooks. It swallows its own exceptions by design: a malformed payload
  must never take down an editing session.

---

## Development setup

```bash
git clone https://github.com/ujjalsharma100/agent-trace-cli
cd agent-trace-cli

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Editable install with the dev extras (build, pytest, hypothesis, jsonschema)
pip install -e ".[dev]"

agent-trace --version
```

The console entry point is declared in `pyproject.toml`
(`agent-trace = agent_trace.cli:main`), so an editable install puts the
`agent-trace` command on your PATH pointing at your working tree.

> The repository `install.sh` is for **end users** (it copies a release into
> `~/.agent-trace/`). For development, use the editable `pip install` above so
> your edits take effect immediately.

---

## Running the tests

Tests live under `tests/` and run with **pytest**:

```bash
pytest                      # whole suite
pytest tests/test_blame.py  # one module
pytest -k codex             # filter by name
```

Notes:

- Tests isolate state by pointing **`AGENT_TRACE_HOME`** at a temporary
  directory, so running them never touches your real `~/.agent-trace/` data.
- Property-based tests (`test_schemas_property.py`) use **hypothesis** and
  validate generated records against the JSON Schemas in
  `agent_trace/schemas/`.
- The remote end-to-end tests (`tests/test_e2e_remote.py`) expect an
  `agent-trace-service` instance; see `tests/docker-compose.test.yml`. The rest
  of the suite runs fully offline.

Please run the suite (and add tests) before opening a pull request.

---

## Repository layout

The pieces you are most likely to touch:

| Path | What lives there |
|------|------------------|
| `agent_trace/cli.py` | Argparse command surface; dispatch to handlers. |
| `agent_trace/hooks/` | **Coding-agent adapters** + the registry (`__init__.py`), plus git-hook setup (`git.py`). |
| `agent_trace/hooks/base.py` | The `CodingAgentAdapter` contract — the authoritative interface. |
| `agent_trace/record.py` | Hook dispatcher (`record_from_stdin`) and shared trace translators. |
| `agent_trace/trace.py` | `create_trace(...)` — the single canonical trace builder. |
| `agent_trace/rules.py` | Prebuilt agent rules (`AVAILABLE_RULES`). |
| `agent_trace/blame.py`, `ledger.py` | Deterministic attribution. |
| `agent_trace/sync.py`, `remote.py` | HTTP remotes and push/pull/sync. |
| `agent_trace/schemas/*.schema.json` | Trace-record and git-note schemas. |
| `tests/`, `tests/fixtures/<agent>/` | Test suite and per-agent hook payload fixtures. |

For the conceptual model behind adapters, read
[`docs/concepts/harnesses.md`](docs/concepts/harnesses.md).

---

## Adding a coding-agent harness

agent-trace integrates with each coding agent through a single abstraction: a
**`CodingAgentAdapter`**. One adapter file is the *only* place that contains
agent-specific knowledge — hook config paths, hook event names, rule
directories, runtime detection. Every other surface (`record`, `doctor`,
`status`, `rule`, the `--tool` choices, the `tool.name` on emitted traces)
walks the adapter registry, so **adding an agent is one new file plus one line
of registration** — nothing else should need to change.

The authoritative contract is [`agent_trace/hooks/base.py`](agent_trace/hooks/base.py).
A fully worked example (OpenAI Codex CLI, including its `notify` wrapper) is in
[`docs/concepts/harnesses.md`](docs/concepts/harnesses.md). Use this section as
the step-by-step checklist.

### What an adapter owns

| Concern | Member |
|---------|--------|
| Registry key used by `--tool <name>` and `tool.name` | `name` *(required)* |
| Where the agent's global hook config lives | `global_config_path()` *(required)* |
| Install / remove / inspect hooks | `inject(...)`, `remove()`, `is_installed(...)` *(inject/remove required)* |
| Hook events → trace translators | `EVENTS: {event_name: handler}` |
| Session-end / summary classification | `summary_only_events`, `summary_then_trace_events` |
| Where prebuilt rules go in a repo | `rules_dir`, `rule_extension` |
| Recognising the agent at runtime | `detect_tool_info()` |
| Human-facing label in `doctor` / `status` | `display_name` |
| Where the agent stores transcript / workspace paths | `transcript_env_vars`, `project_dir_env_vars` |

Only `name`, `global_config_path()`, `inject()`, and `remove()` are required;
everything else is optional and defaults to a no-op. Implement just what the
agent actually exposes.

### Step 1 — Create the adapter

Add `agent_trace/hooks/<agent>.py` with a subclass of `CodingAgentAdapter`:

```python
from __future__ import annotations

import os
from pathlib import Path

from .base import AGENT_TRACE_CMD, CodingAgentAdapter

MYAGENT_GLOBAL = Path.home() / ".myagent" / "hooks.json"


def _TurnComplete(d: dict):
    """Translate a raw hook payload into a canonical trace record."""
    from ..trace import create_trace, resolve_file_project
    # ... pull file path / content / metadata out of ``d`` ...
    return create_trace(...), "MyAgentTurnComplete"


class MyAgentAdapter(CodingAgentAdapter):
    name = "myagent"
    display_name = "My Agent"
    rules_dir = ".myagent/rules"
    rule_extension = ".md"

    EVENTS = {"MyAgentTurnComplete": _TurnComplete}

    def global_config_path(self) -> Path:
        return MYAGENT_GLOBAL

    def detect_tool_info(self):
        ver = os.environ.get("MYAGENT_VERSION")
        return {"name": "myagent", "version": ver} if ver else None

    def inject(self, record_invocation, project_dir=None, *, global_install=False):
        # Idempotently write the agent's hook config so its events get piped
        # to ``record_invocation`` (which is "agent-trace record").
        ...

    def remove(self) -> bool:
        # Remove ONLY agent-trace's own entries from the global config.
        ...
```

Translators may live in the adapter module (as above) or in `record.py` —
whatever's cleanest. What matters is that each `EVENTS` value is a callable
returning `(trace_dict_or_None, event_name)` and that it builds the trace with
**`create_trace(...)`** so every record shares one schema.

### Step 2 — Register it

Add two lines at the bottom of
[`agent_trace/hooks/__init__.py`](agent_trace/hooks/__init__.py):

```python
from .myagent import MyAgentAdapter
register_adapter(MyAgentAdapter())
```

Registration order controls the order tools appear in CLI choices and `doctor`
output. That's the only wiring required — `doctor`, `status`,
`hooks setup-global --tool myagent`, `rule add … --tool myagent`, the `--tool`
argparse choices, and the `tool.name` field all pick the adapter up
automatically.

### Step 3 — (Optional) Prebuilt rules

If the agent supports project-local "rules" files, declare `rules_dir` /
`rule_extension` (Step 1) and add a body for your `name` under the relevant
entry in `AVAILABLE_RULES` in
[`agent_trace/rules.py`](agent_trace/rules.py). Tools not keyed for a given rule
simply skip it.

### Step 4 — Add a fixture and a test

This is how the event format is documented and kept honest:

1. Drop a real hook payload under `tests/fixtures/<agent>/` — this becomes the
   authoritative example of the JSON your adapter accepts.
2. Add `tests/test_<agent>_adapter.py` (see `tests/test_codex_adapter.py` as a
   template). A good adapter test:
   - asserts the adapter appears in `hooks.iter_adapters()`,
   - exercises `inject` / `remove` against a temp config,
   - pipes the fixture through `record` and asserts the resulting trace's
     metadata (project, tool name, line ranges).

### Design rules for adapters

- **Stdlib only.** Agent configs are JSON, TOML (string-edited — we don't import
  a TOML library), or shell scripts. No new runtime dependencies.
- **Idempotent install.** `inject(...)` may be called repeatedly; detect an
  existing `agent-trace record` entry and don't duplicate it.
- **Conservative remove.** `remove()` deletes **only** what agent-trace owns
  (its own markers / hook entries whose command contains `agent-trace record`).
  Never clobber unrelated agent configuration.
- **No network in adapters.** Adapters write local files only. Sync lives in
  `agent_trace/sync.py` and never runs inside a hook.

---

## Coding standards

- Match the surrounding style; keep modules small and focused.
- No third-party imports in `agent_trace/` runtime code (stdlib only).
- New or changed trace output must conform to the JSON Schemas in
  `agent_trace/schemas/`; the property tests will check this.
- Prefer small, self-contained commits with clear messages.

---

## Pull requests

1. Fork the repo and create a feature branch.
2. Make your change with tests; run `pytest` and make sure it's green.
3. Update the relevant docs under `docs/` (and the README, if user-facing
   behavior changed).
4. Open a PR describing the change and the motivation. For a new harness,
   mention which agent and link the upstream hook documentation you relied on.

Bug reports and feature requests are welcome via GitHub issues — for a bug,
include `agent-trace --version`, `agent-trace doctor` output (with URLs/tokens
redacted), and whether you use global or project hooks.

By contributing, you agree that your contributions are licensed under the
project's [Apache 2.0 License](LICENSE).
