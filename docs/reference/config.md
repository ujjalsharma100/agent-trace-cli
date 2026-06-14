# Command: `config`

```text
agent-trace config <ACTION> ...
```

**Purpose:** Inspect or mutate **persisted** configuration (global + current project) without going through the full **`reset`** wizard.

`ACTION` is **required** and must be one of **`show`**, **`set`**, **`reset`**.

---

## `config show` {#show}

```text
agent-trace config show [--json]
```

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--json`** | flag | off | Emit the same snapshot as JSON for scripting. |

**Output:** Default terminal view is **aligned key/value lines** using the same **dotted field names** as `config set` (for example `notes.all-session-conversations`, `summary.command`). Nested structures that are not single `config set` keys (remotes, nested `auth`, optional `global.telemetry.*`) are shown with dotted paths for readability. Tokens are **redacted** (`(set)` / `(anonymous id)`). Use **`--json`** for the full nested snapshot as JSON (scripting, debugging).

**Exit:** `0` on success.

---

## `config set` {#set}

```text
agent-trace config set <field> <value>
```

| Positional | Allowed values | Purpose |
|------------|----------------|---------|
| **`field`** | One of the documented [Configuration fields](../configuration.md#cli-agent-trace-config-set-field-value) (`notes.enabled`, `summary.command`, …) | Which key to write. |
| **`value`** | String (booleans and ints parsed per field) | New value. |

**Errors:** Unknown field → stderr message, exit **1**. Invalid boolean → **1**. Blank `summary.command` → **1**. Non-positive `summary.timeout-seconds` → **1**.

**Project requirement:** Project-scoped fields require an **initialized** project (`init`); otherwise the command exits with guidance to run `init`.

---

## `config reset` {#reset}

```text
agent-trace config reset <field> [--yes]
```

| Positional | Allowed values | Purpose |
|------------|----------------|---------|
| **`field`** | Any **set**-able field **or** aggregate `notes` / `summary` | What to reset. See [Configuration — reset targets](../configuration.md#cli-agent-trace-config-reset-field-yes). |

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--yes`** | flag | off | Non-interactive reset where implemented (bypasses some confirmation / interactive prompts). |

**Behavior highlights:**

- Resetting **`notes`** prompts individually for each notes boolean with **defaults** matching shipped `_DEFAULT_NOTES_CONFIG`.
- Clearing secrets / URLs may still ask for confirmation unless `--yes` covers that path — treat `--yes` as “automation mode” but verify in your version’s `cli.py` if you embed in CI.

**Exit:** `0` on success; **`1`** on unknown target or validation errors.

---

## Usage discovery

```bash
agent-trace config --help
agent-trace config show --help
agent-trace config set --help
agent-trace config reset --help
```

The argparse `choices=` list for `field` is derived from `_CONFIG_FIELDS` / `_CONFIG_RESET_TARGETS` in source and always matches the running binary.
