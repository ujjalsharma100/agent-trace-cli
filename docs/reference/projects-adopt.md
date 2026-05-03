# Commands: `projects`, `adopt`

Lightweight **registry** utilities for discovering `project_id` values and metadata stored under **`AGENT_TRACE_HOME/projects.json`** (implementation evolves — treat output as diagnostic, not a public API unless `--json` guarantees are documented for your release).

---

## `projects` {#projects}

```text
agent-trace projects
agent-trace projects show <project_id>
```

### `projects` (no args)

Lists registered projects as **two-column text**: `project_id` and `canonical_root`.

**Stdout:** Human table; prints `No registered projects.` when empty.

**Exit:** `0`.

### `projects show <project_id>`

```text
agent-trace projects show <project_id>
```

Prints a **JSON object** combining `project_id` with the stored registry record (pretty-printed, indent 2).

**Exit:** **`1`** if the id is unknown.

**Invalid argv:** If you pass stray tokens other than the `show <id>` pattern, stderr usage + exit **`1`**.

---

## `adopt` {#adopt}

```text
agent-trace adopt [PATH]
```

| Positional | Default | Description |
|------------|---------|-------------|
| **`PATH`** | `.` (cwd) | Directory that must resolve to a **git** working tree root (any path inside the repo is accepted; resolved to repo root). |

**Purpose:** Ensure registry metadata exists and print the **stable `project_id`** (anchor-based after init).

**Errors:** Not a git repo → stderr + exit **`1`**.

**Stdout:** Single line `project_id` string suitable for scripting:

```bash
pid="$(agent-trace adopt /path/to/repo)"
echo "$pid"
```

---

## When to use which

| Need | Command |
|------|---------|
| Human browsing registered roots | `projects` |
| Machine metadata JSON | `projects show <id>` |
| CI variable for `AGENT_TRACE_HOME/projects/<pid>` | `adopt` |

---

## Related

- [Project identity](../concepts/project-identity.md)
