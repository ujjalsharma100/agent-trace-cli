# Commands: `init`, `status`, `reset`, `doctor`

---

## `init` {#init}

```text
agent-trace init
```

**Purpose:** Initialize agent-trace for the **current working directory’s** git repository in a **non-interactive** way (similar philosophy to `git init` — no questionnaire by default).

**Typical effects:**

- Creates / updates **per-project configuration** under `AGENT_TRACE_HOME`.
- Writes the **`.git/agent-trace-id`** anchor (opaque id) when needed so worktrees share storage.
- Installs **git hooks** (`post-commit` → `commit-link`, `post-rewrite` → `rewrite-ledger`) when applicable.
- When **`origin`** exists, attempts to configure **git notes refspecs** for `refs/notes/agent-trace`.
- May install **per-project** Cursor/Claude hooks **only if** global hooks are not already present (so you are not duplicative).

**Arguments:** none.

**When to run:** Once per clone (or after you intentionally removed project config). Safe to re-run in many cases; see messages from the tool for destructive edge cases.

**See also:** [Getting started](../getting-started.md), [Project identity](../concepts/project-identity.md).

---

## `status` {#status}

```text
agent-trace status
```

**Purpose:** Print a **concise operational dashboard**: `project_id`, on-disk paths, approximate counts, hook states, remote/default hints, and whether data is waiting to push.

**Arguments:** none.

**Exit codes:** `0` on success; non-zero if the tool cannot resolve the environment meaningfully (rare).

**Audience:** Humans in terminal; not primarily a JSON API (use `config show --json` for structured config).

---

## `reset` {#reset}

```text
agent-trace reset
```

**Purpose:** **Interactive** reconfiguration — prompts for notes sections, summary command preferences, and hook reinstallation choices. **Remotes** are *not* the focus here; use `agent-trace remote` separately.

**Arguments:** none (all choices happen via prompts).

**Contrast:** `agent-trace config reset …` resets **individual fields** or groups with narrower scope.

---

## `doctor` {#doctor}

```text
agent-trace doctor
```

**Purpose:** Validate **hooks**, **configuration files**, **storage paths**, **remotes**, and **optional external tools** (such as whatever your summary preset requires). Prints actionable guidance.

**Arguments:** none.

**When to run:** After install, after pulling teammate hook changes, or when `blame`/`context` behave unexpectedly.
