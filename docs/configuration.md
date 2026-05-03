# Configuration

agent-trace splits configuration into **global** (user-wide) and **per-project** layers. You can view and edit values through **`agent-trace config`**, interactive **`reset`**, or by editing JSON files directly (advanced).

---

## Where files live

| Scope | Path | Contents |
|-------|------|----------|
| **Global** | `$AGENT_TRACE_HOME/config.json` | Auth token, optional global booleans. |
| **Project** | `$AGENT_TRACE_HOME/projects/<project_id>/project-config.json` | Notes toggles, summary settings, `remote.default`, etc. |

`AGENT_TRACE_HOME` defaults to **`~/.agent-trace`**.

---

## CLI: `agent-trace config show`

Prints a **redacted** snapshot:

- Project id, data directory, project config, remotes (tokens masked).
- Global config (sensitive keys redacted).
- Hook installation status (global vs project for Cursor/Claude; git `post-commit` / `post-rewrite` detection).

| Flag | Purpose |
|------|---------|
| **`--json`** | Machine-readable JSON of the same snapshot structure. |

---

## CLI: `agent-trace config set <field> <value>`

Sets a single field. **Boolean** values accept human-friendly tokens (all case-insensitive):

**Truthy:** `1`, `true`, `yes`, `y`, `on`, `enable`, `enabled`  
**Falsy:** `0`, `false`, `no`, `n`, `off`, `disable`, `disabled`

### Settable fields (exhaustive)

| Field | Scope | Value type | Effect |
|-------|--------|------------|--------|
| **`notes.enabled`** | project | bool | Master toggle for git-notes feature integration in workflows that consult project config. |
| **`notes.include-ledger`** | project | bool | Whether note-building includes **inline ledger** payload (larger notes). Default **false** in shipped defaults. |
| **`notes.include-summary`** | project | bool | Include **summary** map in notes when building. |
| **`notes.include-prompts`** | project | bool | Include **prompts** array in notes when building. |
| **`notes.all-session-conversations`** | project | bool | Include **all_session_conversations** staging window section. |
| **`summary.enabled`** | project | bool | Whether session-end summarization path is enabled in config. |
| **`summary.command`** | project | string | Executable + args: **stdin** = raw transcript text, **stdout** = summary text. Must be non-empty. Setting this also forces **`summary.enabled`** true in the implementation. |
| **`summary.timeout-seconds`** | project | int | Positive integer timeout for summarizer subprocess. |
| **`remote.default`** | project | string | Name of the default **`agent-trace remote`** entry used when `--remote` is omitted on sync commands. |
| **`global.auth-token`** | global | string | Stores **`auth_token`** in global config (same logical value as `set globaluser`). |
| **`global.capture-detached-edits`** | global | bool | Advanced: capture edits outside a normal git worktree layout when enabled in global config. |

Examples:

```bash
agent-trace config set notes.include-ledger true
agent-trace config set summary.timeout-seconds 60
agent-trace config set remote.default origin
```

---

## CLI: `agent-trace config reset <field> [--yes]`

Resets one field or **logical group** to defaults.

### Reset targets

Includes **every field** from the table above **plus** the aggregate keys:

| Target | Behavior |
|--------|----------|
| **`notes`** | Interactive re-prompt for all `notes.*` booleans with shipped defaults as Enter-key defaults. |
| **`summary`** | Removes the entire **`summary`** object from project config. |

Other fields:

- **Boolean fields** — interactive prompt unless **`--yes`** is inappropriate; for simple bool resets the implementation prompts with defaults.
- **`summary.command`**, **`summary.timeout-seconds`**, **`remote.default`**, **`global.auth-token`** — confirmation / clear semantics as implemented in `cli.py`.

| Flag | Applies to | Purpose |
|------|------------|---------|
| **`--yes`** | non-interactive automation | For supported reset paths, skips interactive confirmation where implemented (see `config reset` in the [config reference](reference/config.md)). |

---

## JSON key names vs CLI field names

CLI uses **dotted keys with hyphens** (for example `notes.include-ledger`). On disk, project config uses **snake_case** nested objects (for example `"include_ledger"` under `"notes"`). The `config set` command performs the mapping — you should **not** need to hand-edit for normal operation.

---

## Related commands

- **`agent-trace reset`** — broader interactive reconfiguration wizard (different from `config reset`).
- **`agent-trace set globaluser` / `remove globaluser`** — thin wrappers focused on the auth token; see [set & remove](reference/set-remove-globaluser.md).

---

## See also

- [Environment variables](environment-variables.md) — `AGENT_TRACE_TOKEN`, `AGENT_TRACE_HOME`, install-time vars.
- [Data on disk](data-on-disk.md) — every JSONL file you will see.
