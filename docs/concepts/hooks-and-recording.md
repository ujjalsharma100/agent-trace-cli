# Concepts — Hooks & recording

Hooks are how agent-trace **learns** what the agent did: which files changed, which commands ran, where transcripts live, and session boundaries. This page describes **behavior and intent**; exact flags for CLI commands live in the [reference](../reference/index.md).

---

## Two hook layers

### 1. Agent hooks (Cursor / Claude Code)

These call **`agent-trace record`** with a JSON document on **stdin**.

| Installation style | Location (typical) |
|--------------------|--------------------|
| **Global** (recommended) | Cursor: `~/.cursor/hooks.json` — Claude Code: `~/.claude/settings.json` |
| **Per-project** | `<repo>/.cursor/hooks.json` or `<repo>/.claude/settings.json` |

**Global hooks** run for every workspace. The recorder resolves the correct **git root from the file being edited**, not from the agent’s arbitrary cwd. That means:

- Edits inside an **initialized** repo are recorded for that project.
- Edits **outside** any initialized repo are **silently ignored** (by design — you do not get errors in the agent UI).

Install global hooks with:

```bash
agent-trace hooks setup-global
```

### 2. Git hooks

Installed under **`.git/hooks/`** (subject to your `core.hooksPath` if customized):

| Hook | Invokes | Role |
|------|-----------|------|
| **`post-commit`** | `agent-trace commit-link` | Associates the new commit with traces and **builds the ledger**. |
| **`post-rewrite`** | `agent-trace rewrite-ledger` | After rebase/amend, **rewrites ledger SHAs** to match new commit ids. |

These are installed during **`agent-trace init`** when git hook setup is selected / applicable.

---

## Event categories

Roughly:

1. **Trace-producing events** — file edits, shell executions, session start/end (exact event names differ between Cursor and Claude Code). Each produces or updates trace records including **per-line content hashes** used later for attribution.
2. **Conversation sync events** — fire after an assistant response or stop hook; they **refresh** knowledge of where the **transcript file** lives so `context` can open it later. They are **not** a substitute for trace events.

---

## `agent-trace record`

This command **reads stdin** and appends to **`traces.jsonl`**. It is **not** intended for interactive human use during normal development; it exists as the stable entrypoint hooks call.

The implementation deliberately **swallows exceptions** so a malformed payload cannot take down your coding session.

---

## Merging with existing hook configuration

When installing hooks, agent-trace attempts to **merge** with existing JSON — it does not blindly overwrite unrelated hook commands your team already configured.

---

## Verification

Use:

```bash
agent-trace hooks status   # global agent hooks
agent-trace doctor         # broader health: git hooks, summary command, remotes, etc.
agent-trace status         # project-centric overview
```

---

## Further reading

- [record, commit-link, rewrite-ledger](../reference/record-commit-link-rewrite.md) — stdin/stdout contracts and when to run manually (almost never).
- [Hooks command](../reference/hooks.md) — `--tool`, removal, and status output.
