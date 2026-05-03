# Command reference — index

The program name is **`agent-trace`** (short alias **`at`** when installed). Invoking **`agent-trace`** with no subcommand prints **top-level help** and exits **0**.

---

## Global options

| Option | Purpose |
|--------|---------|
| **`--version`** | Print version string and exit. |

There is no global `--verbose` flag today; individual commands print human-readable diagnostics to stderr on error.

---

## Command matrix

| Command | Summary |
|---------|---------|
| **[init](init-status-reset-doctor.md#init)** | Initialize tracing for the current git project (zero-prompt). |
| **[doctor](init-status-reset-doctor.md#doctor)** | Health check: hooks, config, storage, remotes, optional tools. |
| **[status](init-status-reset-doctor.md#status)** | Git-style overview: project id, paths, counts, sync/hook hints. |
| **[reset](init-status-reset-doctor.md#reset)** | Interactive reconfiguration wizard. |
| **[config](config.md)** | `show` \| `set` \| `reset` for persisted configuration. |
| **[hooks](hooks.md)** | `setup-global` \| `remove-global` \| `status` for agent hooks. |
| **[record](record-commit-link-rewrite.md#record)** | Read one JSON trace event from stdin (hook entrypoint). |
| **[commit-link](record-commit-link-rewrite.md#commit-link)** | Post-commit: link commit, build ledger. |
| **[rewrite-ledger](record-commit-link-rewrite.md#rewrite-ledger)** | Post-rewrite: remap ledger SHAs. |
| **[viewer](viewer.md)** | Launch file viewer UI. |
| **[blame](blame.md)** | Ledger-only attribution for a file. |
| **[context](context.md)** | Conversation snippets / full transcript for AI ranges. |
| **[rule](rule.md)** | `add` \| `remove` \| `show` \| `list` prebuilt agent rules. |
| **[set](set-remove-globaluser.md)** | `set globaluser <token>`. |
| **[remove](set-remove-globaluser.md)** | `remove globaluser`. |
| **[remote](remote.md)** | Named HTTP remotes (`add`, `list`, …). |
| **[push](push-pull-sync.md#push)** | Upload local artifacts. |
| **[pull](push-pull-sync.md#pull)** | Download remote artifacts. |
| **[sync](push-pull-sync.md#sync)** | Push then pull. |
| **[projects](projects-adopt.md#projects)** | List registry rows or `projects show <id>`. |
| **[adopt](projects-adopt.md#adopt)** | Register repo, print `project_id`. |
| **[notes](notes.md)** | Git notes on `refs/notes/agent-trace`. |
| **[summary](summary.md)** | Pluggable transcript summarization. |

---

## Reading guide

- **Human operators** — follow [Getting started](../getting-started.md) then skim **Concepts** for mental model.
- **Automation / CI** — prefer `--json` where available (`config show`, `blame`, `context`) and **`blame --require-attribution`** when enforcing provenance.
- **Authors of wrappers** — rely on **`--help`** on each subcommand for argparse text; this site expands purpose and edge cases.

---

## Conventions used in this reference

- **`<arg>`** — required placeholder.
- **`[optional]`** — may be omitted.
- **Exit codes** — **`0`** success unless a command documents non-zero (for example `blame --require-attribution`).
