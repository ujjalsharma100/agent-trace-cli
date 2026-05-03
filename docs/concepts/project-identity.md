# Concepts — Project identity & storage

Every trace file, ledger row, and config file lives under **`AGENT_TRACE_HOME`** (default **`~/.agent-trace/`**), inside **`projects/<project_id>/`**. Understanding **`project_id`** resolution is essential when you have worktrees, renamed directories, or multiple repos in one editor session.

---

## AGENT_TRACE_HOME

| Path | Role |
|------|------|
| `<home>/config.json` | **Global** settings: auth token, optional flags. |
| `<home>/projects.json` | **Registry** metadata (optional enrichment; see `projects` / `adopt`). |
| `<home>/projects/<project_id>/` | **All per-project** JSONL, `project-config.json`, sync cursors, etc. |

Override the root with the **`AGENT_TRACE_HOME`** environment variable (used heavily in tests; also valid for power users who want a separate disk).

---

## How `project_id` is resolved

When you run a command inside a git working tree, resolution follows this order (simplified from `storage.resolve_project_id`):

1. **Anchor file** — **`<git-common-dir>/agent-trace-id`**, a single line containing the opaque id (for example `at-` followed by 32 hex characters).  
   - **Created** on `init` (or whenever `resolve_project_id(..., create=True)` runs without an existing anchor).  
   - **Shared** across linked **git worktrees** because all worktrees share the same `git-common-dir`.
2. **Path-derived fallback** — If there is **no anchor** yet (repository never initialized with agent-trace), the tool falls back to a sanitized form of the **realpath** of the repo root (legacy-style `-Users-you-src-foo`). This lets `status` / `blame` resolve to *some* directory even before `init`.
3. **Non-git paths** — Outside a git repo, a path-derived id is still produced for detached-edit and test scenarios.

**Important:** A **fresh clone** of a repository gets a **new anchor** when initialized; teammates rely on **git notes** (and/or **remote sync**) to share attribution context, not on matching local directory paths.

---

## adopt vs init

| Command | Purpose |
|---------|---------|
| **`agent-trace adopt [path]`** | Registers metadata and prints the **stable** `project_id` for a git root **without** performing full project setup (no interactive/git hook story). Useful for scripting and registry visibility. |
| **`agent-trace init`** | Full project setup: config, hooks as applicable, git hooks, notes refspec wiring, anchor creation. |

See the [projects & adopt](../reference/projects-adopt.md) reference.

---

## Multi-repo working directories

Some editor layouts open a parent folder that contains multiple git roots. Commands that need a repo context accept **`--project`** (or equivalent) with either:

- A path to the **git repository root**, or  
- A **`project_id`** string registered in the workspace (disambiguation depends on the command; see each reference page).

When in doubt, pass **`--project /absolute/path/to/repo`**.

---

## Related configuration

- **`remote.default`** — which named remote `push` / `pull` / `sync` prefer when you omit `--remote`.  
- Per-project config is stored as JSON alongside data files — see [Configuration](../configuration.md).
