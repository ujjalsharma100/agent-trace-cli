# Command: `hooks`

```text
agent-trace hooks <ACTION> ...
```

**Purpose:** Install or remove **global** agent hooks for **Cursor** and **Claude Code** under the user’s home directory so **every** workspace can emit traces without per-repo hook files.

If you run **`agent-trace hooks`** without `ACTION`, the CLI prints a short usage line to stdout (see `cmd_hooks`).

---

## `hooks setup-global` {#setup-global}

```text
agent-trace hooks setup-global [--tool TOOL ...]
```

| Option | Short | Repeatable | Values | Default | Purpose |
|--------|-------|------------|--------|---------|---------|
| **`--tool`** | **`-t`** | yes (`action='append'`) | `cursor`, `claude` | all tools | Restrict installation to the listed tools only. |

**Effects:**

- Merges hook entries into **`~/.cursor/hooks.json`** and/or **`~/.claude/settings.json`** without clobbering unrelated hooks.
- Prints per-tool success (`-> Global <tool> hooks configured`) or failure (`!! Failed…`).

**Exit:** `0` even if a tool fails (check stdout for `!!` lines). This matches “best effort installer” semantics.

---

## `hooks remove-global` {#remove-global}

```text
agent-trace hooks remove-global [--tool TOOL ...]
```

Same **`--tool` / `-t`** semantics as setup, but **removes** agent-trace entries that were previously merged.

**Stdout:** `-> Global <tool> hooks removed` or `-- Global <tool> hooks were not present`.

---

## `hooks status` {#status}

```text
agent-trace hooks status
```

**Purpose:** Print whether global hook files contain the expected agent-trace hook commands.

**Output example shape:**

```text
Global hooks:
  Cursor:     configured  (~/.cursor/hooks.json)
  Claude Code: not configured  (~/.claude/settings.json)
```

**Exit:** `0`.

---

## Design note: global vs `init`

**Recommended workflow:** `hooks setup-global` **once per machine**, then `agent-trace init` in each repository for **git hooks**, **project config**, and **notes refspecs**. `init` skips redundant per-project agent hooks when globals already exist.

See [Hooks & recording](../concepts/hooks-and-recording.md).
