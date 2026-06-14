# Coding-agent harnesses (adapters)

`agent-trace` integrates with multiple coding agents (Cursor, Claude
Code, Codex CLI, ...) through a single abstraction: a
**`CodingAgentAdapter`**. Each adapter is the *only* place that
contains agent-specific knowledge — hook config paths, hook event
names, rule directories, runtime detection. The CLI, `record`, `doctor`,
`status` and `rule` surfaces all walk the adapter registry, so adding
support for a new harness is a single file plus one line.

> **Contributing a harness?** This page is the conceptual reference for the
> adapter model. For the step-by-step workflow — dev setup, the add-a-harness
> checklist, fixtures, tests, and the PR process — see
> [`CONTRIBUTING.md`](https://github.com/ujjalsharma100/agent-trace-cli/blob/main/CONTRIBUTING.md) in the repository root.

## What an adapter owns

| Concern | Adapter member |
|---------|----------------|
| Where the agent's hook config lives | `global_config_path()` |
| Installing / removing hooks | `inject(...)`, `remove()`, `is_installed()` |
| Hook events the agent emits | `EVENTS: {event_name: handler}` |
| Translating a hook payload into a trace | the handler functions |
| Where prebuilt rules go in a repo | `rules_dir`, `rule_extension` |
| How to recognise the agent at runtime | `detect_tool_info()` |
| Display name for human-facing output | `display_name` |

The full contract is in `agent_trace/hooks/base.py`.

## Built-in adapters

| Adapter | Hook config | Hook events |
|---------|-------------|-------------|
| Cursor (`cursor`) | `~/.cursor/hooks.json` | `afterFileEdit`, `afterTabFileEdit`, `afterShellExecution`, `sessionStart`, `sessionEnd` |
| Claude Code (`claude`) | `~/.claude/settings.json` | `PostToolUse` (Write/Edit/Bash/MultiEdit/NotebookEdit), `SessionStart`, `SessionEnd` |
| Codex CLI (`codex`) | `~/.codex/config.toml` (the `notify` array) | `CodexTurnComplete`, `CodexSessionStart` |

## How the Codex adapter works

OpenAI's Codex CLI exposes a single hook surface called
[`notify`](https://github.com/openai/codex): a program declared in
`~/.codex/config.toml` that Codex spawns when an agent turn finishes.
Codex passes the JSON payload as the program's last argv:

```json
{
  "type": "agent-turn-complete",
  "turn-id": "...",
  "input-messages": [...],
  "last-assistant-message": "..."
}
```

`agent-trace hooks setup-global --tool codex` writes a small shell
wrapper into Codex's `notify`. The wrapper:

1. Tags the JSON payload with `hook_event_name = CodexTurnComplete`
   (so the dispatcher in `record.py` knows which adapter owns it).
2. Pipes the augmented JSON into `agent-trace record`.

For testing, you can skip the wrapper and pipe a JSON object directly:

```bash
echo '{"hook_event_name":"CodexTurnComplete","turn-id":"t1",
       "last-assistant-message":"...","cwd":"/path/to/repo"}' \
  | agent-trace record
```

A trace lands under `~/.agent-trace/projects/<id>/traces.jsonl`.

## Adding a new harness

To support a new coding agent (Aider, OpenCode, Continue, ...):

1. **Create `agent_trace/hooks/<agent>.py`** with a class subclassing
   `CodingAgentAdapter`. Implement `inject`, `remove`,
   `global_config_path`, and any of the optional members you need:

   ```python
   from .base import AGENT_TRACE_CMD, CodingAgentAdapter

   AIDER_GLOBAL = Path.home() / ".aider" / "config.yml"

   class AiderAdapter(CodingAgentAdapter):
       name = "aider"
       display_name = "Aider"
       rules_dir = ".aider/rules"
       rule_extension = ".md"

       @property
       def EVENTS(self):
           from .. import record as _record
           return {"AiderTurnComplete": _record._aider_TurnComplete}

       def global_config_path(self) -> Path:
           return AIDER_GLOBAL

       def detect_tool_info(self):
           ver = os.environ.get("AIDER_VERSION")
           return {"name": "aider", "version": ver} if ver else None

       def inject(self, record_invocation, project_dir=None, *, global_install=False):
           # write the agent's hook config so events are piped to
           # ``record_invocation``. Must be idempotent.
           ...

       def remove(self) -> bool:
           # strip agent-trace's entries from the global config.
           ...
   ```

2. **Add event translators** in `agent_trace/record.py`. Each translator
   takes the raw hook payload `dict` and returns
   `(trace_dict_or_None, event_name)`. Use `create_trace(...)` from
   `agent_trace/trace.py` so all traces share one schema.

3. **Register the adapter** at the bottom of
   `agent_trace/hooks/__init__.py`:

   ```python
   from .aider import AiderAdapter
   register_adapter(AiderAdapter())
   ```

4. **(Optional)** add prebuilt rule bodies under the new `name` in
   `agent_trace/rules.py`'s `AVAILABLE_RULES`.

5. **(Optional)** add a fixture under `tests/fixtures/<agent>/` and a
   parametrised test in `tests/test_<agent>_adapter.py` that pipes the
   fixture through `record_from_stdin` and asserts the trace's metadata.

That's it — no other file should need to change. `agent-trace doctor`,
`agent-trace status`, `agent-trace hooks setup-global --tool <agent>`,
`agent-trace rule add <name> --tool <agent>`, the `--tool` argparse
choices, and the `tool.name` field on emitted traces all walk the
registry and pick the new adapter up automatically.

## Design notes

- **Stdlib only.** No third-party deps. Agents' configs are JSON, TOML
  (string-edited; we don't import a TOML library), or shell scripts.
- **Idempotent install.** `inject(...)` may be called repeatedly — the
  adapter must detect existing `agent-trace record` entries and not
  duplicate them.
- **Conservative remove.** `remove()` only deletes what it owns
  (`# agent-trace:notify` markers, hook entries whose `command`
  contains `agent-trace record`). It must never clobber unrelated
  agent config.
- **No network in adapters.** Adapters write local files. Sync runs in
  `agent_trace/sync.py`, never inside a hook.
- **Tests document the event format.** The fixture under
  `tests/fixtures/<agent>/` is the authoritative example of the JSON
  payload `agent-trace record` accepts for that agent.
