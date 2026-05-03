# Data on disk

This reference enumerates **meaningful paths** under **`AGENT_TRACE_HOME`** (default `~/.agent-trace`) and **small** amounts of in-repo state (hooks only — **no** committed agent-trace identity file in the working tree).

---

## Global tree (`$AGENT_TRACE_HOME/`)

| Path | Purpose |
|------|---------|
| **`config.json`** | Global configuration (`auth_token`, optional `capture_detached_edits`, …). |
| **`projects.json`** | Registry of known projects / metadata used by `projects` and adoption flows. |
| **`bin/agent-trace`** | Launcher script created by `install.sh`. |
| **`bin/at`** | Short alias launcher. |
| **`bin/agent-trace-viewer`** | Viewer launcher. |
| **`lib/`** | Installed Python sources (`agent_trace/` package). |
| **`viewer/`** | Installed web viewer assets and backend. |
| **`sessions/`** | Auxiliary session-related storage (implementation detail for session machinery). |
| **`detached/`** | Storage related to detached-edit capture when enabled. |

---

## Per-project tree (`projects/<project_id>/`)

| File | Purpose |
|------|---------|
| **`project-config.json`** | `notes`, `summary`, `remote` defaults — edited via `config` / `init` / `reset`. |
| **`traces.jsonl`** | Append-only trace records from hooks (`record`). |
| **`commit-links.jsonl`** | Associations between git commits and trace / session data. |
| **`ledgers.jsonl`** | Deterministic per-commit attribution ledgers. |
| **`session-state.json`** | Cursor for active session / staging window (implementation detail). |
| **`session-summaries.jsonl`** | Latest summaries per `conversation_url`. |
| **`sync-state.json`** | Push/pull cursors when using HTTP remotes. |
| **`attribution-state.json`** | Attribution-window cursor (last commit timestamp seen). |

The **`project_id`** directory name is a sanitized form of the id string (slashes/colons flattened for safety).

---

## Inside the git directory (anchor)

| Path | Purpose |
|------|---------|
| **`<git-common-dir>/agent-trace-id`** | Single-line opaque **`project_id`**. Shared across worktrees. Created during init / first `create=True` resolution. |

---

## Optional in-repo files (not identity)

These may exist depending on whether you use **project-level** hooks or **rules**:

| Path | Purpose |
|------|---------|
| **`.cursor/hooks.json`** | Cursor hooks when not using global hooks exclusively. |
| **`.claude/settings.json`** | Claude Code hooks when not using global hooks exclusively. |
| **`.cursor/rules/agent-trace-<name>.mdc`** | Cursor rules installed via `agent-trace rule add`. |
| **`.claude/rules/agent-trace-<name>.md`** | Claude rules installed via `agent-trace rule add`. |
| **`.git/hooks/post-commit`**, **`.git/hooks/post-rewrite`** | Git hook scripts invoking `commit-link` / `rewrite-ledger`. |

Nothing in-repo encodes **project_id** for git object portability — the anchor lives **inside** `.git`.

---

## Backup guidance

To back up agent-trace state for a machine:

1. Archive **`$AGENT_TRACE_HOME/projects/`** (or selective project subfolders).
2. Archive **`$AGENT_TRACE_HOME/config.json`** if you rely on stored tokens.
3. Remember that **git notes** on remotes are separate — fetch `refs/notes/agent-trace` on clones.
