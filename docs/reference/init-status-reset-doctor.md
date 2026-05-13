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
agent-trace doctor [--fix] [--dry-run] [--yes]
```

**Purpose:** Validate **hooks**, **configuration files**, **storage paths**, **remotes**, and **optional external tools** (such as whatever your summary preset requires). Prints actionable guidance.

**Options:**

| Flag | Effect |
|------|--------|
| `--fix` | Apply auto-fixes for safe-to-repair issues (re-create missing hook stubs, restore `refs/notes/agent-trace` refspec, etc.). Skips network-level problems like unreachable remotes. |
| `--dry-run` | Print what `--fix` would do, without writing anything. |
| `--yes` | Run `--fix` non-interactively (skip per-fix confirmation). |

**Remote checks:** For every configured remote, `doctor` parses the URL, probes the service health endpoint, and calls `GET /api/v1/auth/whoami` to verify the bound token's org / project scope matches the URL. The output includes one of:

- `OK Remote 'origin' responds at https://… (org=acme, project=myrepo) (…)`.
- `OK Remote 'origin' token matches URL (token org='acme', org-scoped)`.
- `XX Remote 'origin' token/scope mismatch (org_slug_mismatch): …` — fix with `remote set-token` or `remote set-url`.
- `!!  Remote 'origin' service does not implement /auth/whoami` — server is too old; upgrade or accept the soft failure.

**When to run:** After install, after pulling teammate hook changes, after rotating tokens, or when `blame`/`context`/`push` behave unexpectedly.
