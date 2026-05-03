# Command: `rule`

```text
agent-trace rule <ACTION> ...
```

**Purpose:** Install or remove **prebuilt markdown rules** that teach coding agents how to use agent-trace features (today: primarily **`context`** workflows).

`ACTION` is optional at argparse level; if omitted, the CLI prints usage (`rule {add,remove,show,list}`).

---

## `rule list`

```text
agent-trace rule list
```

Prints all **available** prebuilt rule names and descriptions to stdout.

**Exit:** `0`.

---

## `rule show`

```text
agent-trace rule show
```

Lists **currently installed** rules in the **current project** (both Cursor and Claude files are scanned).

**Exit:** `0` even when none installed (prints guidance).

---

## `rule add`

```text
agent-trace rule add <rule_name> --tool <cursor|claude>
```

| Positional | Description |
|------------|-------------|
| **`rule_name`** | Short name (example: **`context-for-agents`**). |

| Option | Short | Required | Values |
|--------|-------|----------|--------|
| **`--tool`** | **`-t`** | **yes** | `cursor`, `claude` |

**Files written:**

| Tool | Path |
|------|------|
| `cursor` | `.cursor/rules/agent-trace-<rule_name>.mdc` |
| `claude` | `.claude/rules/agent-trace-<rule_name>.md` |

**Exit:** `0` on success; **`1`** if unknown rule name or IO error.

---

## `rule remove`

```text
agent-trace rule remove <rule_name> --tool <cursor|claude>
```

Same **`--tool` / `-t`** requirement as `add`. Idempotent messaging if the file was not present.

---

## Available rules (current ship set)

| `rule_name` | Intent |
|-------------|--------|
| **`context-for-agents`** | Teaches agents to call `agent-trace context` with `--json`, interpret `conversation_size`, and delegate large `--full` reads to subagents. |

Additional rules may appear in future releases — always run **`rule list`** on your installed version.

---

## Related

- [context](context.md)
