# Commands: `record`, `commit-link`, `rewrite-ledger`

These three commands form the **ingestion pipeline** from IDE events → JSONL traces → git-linked ledgers. **`record`** is the hot path called on **every** hook firing; the other two are normally invoked from **git hooks**.

---

## `record` {#record}

```text
agent-trace record   # reads JSON from stdin
```

**Purpose:** Deserialize **one** JSON object from **stdin** and append a trace record to the appropriate project’s **`traces.jsonl`**.

**Arguments:** none (all data is stdin).

** stdin contract:** A single JSON object per invocation matching the internal trace schema produced by Cursor/Claude adapters (see repository `schemas/trace-record.schema.json` for shape).

**Failure behavior:** Exceptions are **caught and swallowed** so a malformed payload cannot crash the coding agent. There is intentionally **no** loud stderr in the common failure path.

**Human usage:** Almost never — use hooks. For debugging, you can pipe fixture files:

```bash
agent-trace record < tests/fixtures/cursor/afterfileedit_create.json
```

---

## `commit-link` {#commit-link}

```text
agent-trace commit-link
```

**Purpose:** Run after a commit exists:

- Link the **HEAD commit** (conceptually “current commit”) to relevant traces / session state.
- Append / merge into **`commit-links.jsonl`** as needed.
- Build / append the **attribution ledger** for that commit SHA.

**Arguments:** none.

**Git context:** Expects to be launched from a **git working tree** with appropriate environment (as git hooks provide).

**Manual usage:** Supported for debugging or recovering from a missed hook, but normal workflows rely on **`post-commit`**.

---

## `rewrite-ledger` {#rewrite-ledger}

```text
agent-trace rewrite-ledger   # reads rewrite mapping from stdin
```

**Purpose:** After **`git rebase`**, **`git commit --amend`**, or similar, git supplies **old SHA → new SHA** pairs on stdin; this command updates **`ledgers.jsonl`** (and related structures) so historical rows remain coherent.

**Arguments:** none; **stdin** carries the mapping in the format expected by `rewrite.py` (as produced by git’s `post-rewrite` hook).

**Manual usage:** Rare; prefer letting **`post-rewrite`** call it automatically.

---

## Hook wiring reference (quick)

| Git hook | Command |
|----------|---------|
| `post-commit` | `agent-trace commit-link` |
| `post-rewrite` | `agent-trace rewrite-ledger` |

Agent hooks (Cursor / Claude Code) | `agent-trace record`
