# agent-trace

**agent-trace** is a command-line tool for tracing how AI coding assistants (for example **Cursor** and **Claude Code**) change your repositories. It follows a **local-first** model: hooks write structured events under your home directory, git hooks build a **deterministic attribution ledger** at commit time, and you can optionally **push** and **pull** data to an HTTP remote or share pointers via **git notes** (`refs/notes/agent-trace`).

This site is the **published user manual**: every command, flag, and configuration path the CLI exposes, plus the concepts you need to use it confidently in real teams.

---

## What you can do with agent-trace

| Goal | Primary commands |
|------|------------------|
| Turn tracing on in a repo | [`agent-trace init`](reference/init-status-reset-doctor.md#init) |
| Install hooks once for all projects | [`agent-trace hooks setup-global`](reference/hooks.md) |
| See health and counts | [`agent-trace status`](reference/init-status-reset-doctor.md#status), [`agent-trace doctor`](reference/init-status-reset-doctor.md#doctor) |
| Line-level AI vs human labels | [`agent-trace blame`](reference/blame.md) |
| Pull conversation text behind AI edits | [`agent-trace context`](reference/context.md) |
| Browse files with git + agent blame | [`agent-trace viewer`](reference/viewer.md) |
| Team sync / backup | [`agent-trace remote`](reference/remote.md), [`push` / `pull` / `sync`](reference/push-pull-sync.md) |
| Ship metadata with the repo | [`agent-trace notes`](reference/notes.md) |
| Session-end transcript summaries | [`agent-trace summary`](reference/summary.md) |

---

## Design principles (at a glance)

- **No third-party Python dependencies** — the CLI uses only the Python standard library (Python **3.9+**).
- **Ledger-only blame** — `blame` does not guess from heuristics. If there is no ledger (and no usable inline data in a git note), lines are **UNKNOWN**.
- **Explicit sync** — nothing uploads to a remote during normal editing; you run `push`, `pull`, or `sync` when you want.
- **Stable project identity** — after `init`, identity is anchored in `.git/agent-trace-id` so renames and worktrees behave predictably. See [Project identity](concepts/project-identity.md).

---

## Where to read next

1. **[Getting started](getting-started.md)** — minimal path from zero to first blame.
2. **[Installation](installation.md)** — installer behavior, PATH, viewer, uninstall.
3. **[Command reference](reference/index.md)** — exhaustive per-command pages.
4. **[Configuration](configuration.md)** — every `config set` / `config reset` field.

---

## Specification & source

The tool aligns with the public **[Agent Trace](https://agent-trace.dev/)** specification. Source code lives in the **[agent-trace-cli](https://github.com/ujjalsharma100/agent-trace-cli)** repository on GitHub.

---

## Building this documentation locally

```bash
cd agent-trace-cli
python3 -m venv .venv-docs && source .venv-docs/bin/activate
pip install -r docs/requirements-docs.txt
mkdocs serve
```

Open the URL printed in the terminal (usually `http://127.0.0.1:8000`). To emit static HTML for publishing:

```bash
mkdocs build
```

The output directory is `site/` by default.
