# Data on disk

This reference enumerates **meaningful paths** under **`AGENT_TRACE_HOME`** (default `~/.agent-trace`) and **small** amounts of in-repo state (hooks only — **no** committed agent-trace identity file in the working tree).

---

## Global tree (`$AGENT_TRACE_HOME/`)

| Path | Purpose |
|------|---------|
| **`config.json`** | Global configuration. Holds **per-remote tokens** under `tokens.<project_id>::<remote_name>` (mode `0o600`), the legacy `auth_token` slot, `capture_detached_edits`, telemetry prefs. |
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
| **`remotes.json`** | Named HTTP remotes for this project: URL, derived `org_slug` / `project_slug` / `base_url`, and an `auth.token_ref` (`global:…` / `env:…`). Mode `0o600`. Written by `agent-trace remote add/set-url/set-token/rename/remove`. |
| **`traces.jsonl`** | Append-only trace records from hooks (`record`). |
| **`commit-links.jsonl`** | Associations between git commits and trace / session data. |
| **`ledgers.jsonl`** | Deterministic per-commit attribution ledgers. |
| **`session-state.json`** | Cursor for active session / staging window (implementation detail). |
| **`session-summaries.jsonl`** | Latest summaries per `conversation_url`. |
| **`sync-state.json`** | Per-remote content-id manifests (`synced.trace_ids`, `synced.ledger_shas`, `synced.blob_shas`, …) and paging cursors. Source of truth for "is this pushed?" — losing it forces a re-scan but never drops data. |
| **`attribution-state.json`** | Attribution-window cursor (last commit timestamp seen). |
| **`conversations/<sha[:2]>/<content_sha256>`** | Content-addressed transcript blob cache. Same bytes on every machine that has synced; large transcripts (> 256 KiB) shuttle through `POST /api/v1/blobs` / `GET /api/v1/blobs/<sha>`. |
| **`conversations/_index.json`** | `conversation_id → latest content_sha256` index so `blame` / `context` / viewer always resolve to the freshest snapshot. |

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
| **`.codex/rules/agent-trace-<name>.md`** | Codex CLI rules installed via `agent-trace rule add`. |
| **`.git/hooks/post-commit`**, **`.git/hooks/post-rewrite`** | Git hook scripts invoking `commit-link` / `rewrite-ledger`. |

Nothing in-repo encodes **project_id** for git object portability — the anchor lives **inside** `.git`.

---

## Backup guidance

To back up agent-trace state for a machine:

1. Archive **`$AGENT_TRACE_HOME/projects/`** (or selective project subfolders).
2. Archive **`$AGENT_TRACE_HOME/config.json`** if you rely on stored tokens.
3. Remember that **git notes** on remotes are separate — fetch `refs/notes/agent-trace` on clones.
