# agent-trace CLI

A command-line tool for tracing AI-generated code changes across coding agents like **Cursor** and **Claude Code**.

This implementation is built to the [Agent Trace](https://agent-trace.dev/) specification.

Works in two modes:
- **Local** — traces saved to `.agent-trace/traces.jsonl` in your project (no server needed)
- **Remote** — traces sent to the [agent-trace-service](../agent-trace-service/) for centralized storage

Use **`agent-trace blame <file>`** to see which lines in a file are attributed to AI traces (works in both local and remote mode).

**Zero external dependencies** — uses only the Python standard library (requires Python 3.9+).

---

## Installation

### One-liner (install from GitHub)

```bash
curl -fsSL https://raw.githubusercontent.com/ujjalsharma100/agent-trace-cli/main/install.sh | bash
```

### From the repo (local install)

```bash
git clone https://github.com/ujjalsharma100/agent-trace-cli
cd agent-trace-cli
bash install.sh
```

### What the installer does

1. If run via curl, downloads the repo from GitHub and runs the installer
2. Checks for Python 3.9+
3. Copies Python source to `~/.agent-trace/lib/`
4. Creates an executable at `~/.agent-trace/bin/agent-trace`
5. Copies `.env.example` to `~/.agent-trace/.env` (if no `.env` exists)
6. Adds `~/.agent-trace/bin` to your shell PATH (zsh, bash, or fish)

After installing, restart your shell (or `source ~/.zshrc`) and verify:

```bash
agent-trace --version
```

### Uninstall

```bash
rm -rf ~/.agent-trace/bin ~/.agent-trace/lib
```

Then remove the `# agent-trace` + `export PATH=...` lines from your `~/.zshrc` / `~/.bashrc`.

---

## Commands

### `agent-trace init`

Initialize tracing for the current project. You'll be prompted for:

1. **Storage mode** — `local` or `remote`
2. **Project ID** — (remote only)
3. **Auth Token** — (remote only, skipped if global token is set)
4. **Configure Cursor hook?** — yes/no
5. **Configure Claude Code hook?** — yes/no

```bash
cd my-project
agent-trace init
```

### `agent-trace status`

Show current configuration, trace count (local), or remote connection info.

```bash
agent-trace status
```

### `agent-trace reset`

Re-prompts for all settings (storage mode, project ID, auth token, hooks).

```bash
agent-trace reset
```

### `agent-trace record`

Record a trace from stdin. This is what the hooks call — you don't run this manually.

```bash
echo '{"hook_event_name":"sessionStart",...}' | agent-trace record
```

### `agent-trace commit-link`

Link the current git commit to the traces that were active in this session. Called automatically by the post-commit hook when you have configured git hooks; you can also run it manually after a commit. Required for strong AI attribution (commit-link signal) when using `agent-trace blame`.

```bash
agent-trace commit-link
```

### `agent-trace viewer [--project /path]`

Open the **file viewer** in your browser. The viewer lets you browse the project’s file tree, view file contents, and (in later phases) see git blame and agent-trace blame inline.

- **If the viewer is not installed:** the CLI prints install instructions (e.g. `curl -fsSL .../agent-trace-viewer/install.sh | bash` or run `./install.sh` from `agent-trace-viewer/`).
- **If the viewer is installed:** the CLI launches it; open **http://127.0.0.1:8765** in your browser.

```bash
agent-trace viewer
agent-trace viewer --project /path/to/repo
```

### `agent-trace blame <file>`

Show **AI attribution** for a file: which lines (or segments) are attributed to AI traces, with a confidence tier (1–6) and model/tool info. Works in both **local** and **remote** mode:

- **Local** — Uses `.agent-trace/traces.jsonl` and `.agent-trace/commit-links.jsonl` in the project.
- **Remote** — Sends blame data to the agent-trace-service; uses traces and commit links stored there.

The command runs `git blame --porcelain` on the file, groups lines by commit, then runs the same attribution algorithm (signals, scoring, tiers) locally or via the API. See the service [ATTRIBUTION-ALGORITHM.md](../agent-trace-service/ATTRIBUTION-ALGORITHM.md) for how attribution works.

```bash
agent-trace blame src/utils/parser.ts
agent-trace blame src/utils/parser.ts --line 42
agent-trace blame src/utils/parser.ts --range 10-100
agent-trace blame src/utils/parser.ts --json
agent-trace blame src/utils/parser.ts --min-tier 4   # Only show tier 1–4 (higher confidence)
```

| Option | Short | Description |
|--------|--------|-------------|
| `--line` | `-l` | Blame a single line |
| `--range` | `-r` | Blame a line range (e.g. `10-25`) |
| `--json` | | Output attributions as JSON |
| `--min-tier` | | Minimum tier to show (1–6; default 6). Lower number = only higher-confidence attributions. |

### `agent-trace set globaluser <token>`

Store an auth token globally (`~/.agent-trace/config.json`) so it's used across all projects.

```bash
agent-trace set globaluser eyJhbGci...
```

### `agent-trace remove globaluser`

Remove the global auth token.

```bash
agent-trace remove globaluser
```

---

## Configuration

### Global — `~/.agent-trace/config.json`

```json
{
  "auth_token": "your-token-here"
}
```

### Service URL — `~/.agent-trace/.env`

```bash
# Service URL (default: http://localhost:5000)
AGENT_TRACE_URL=http://localhost:5000
```

Edit this file after install to point at your service. See `.env.example` for reference.

### Project — `.agent-trace/config.json`

Created by `agent-trace init` in each project directory.

**Local mode:**
```json
{
  "storage": "local"
}
```

**Remote mode:**
```json
{
  "storage": "remote",
  "project_id": "my-project",
  "service_url": "http://localhost:5000"
}
```

### Resolution order

| Setting | Priority |
|---------|----------|
| Auth token | `AGENT_TRACE_TOKEN` env > global config > project config |
| Service URL | `AGENT_TRACE_URL` env / `.env` > project config > default (`http://localhost:5000`) |

---

## How hooks work

When `agent-trace init` configures hooks, it writes two kinds of events into Cursor and Claude Code config:

1. **Trace-recording hooks** — after file edits, shell runs, and session start/end. Each event produces a trace record (stored locally or sent to the remote service).
2. **Conversation-sync hooks** — after the assistant has finished a full response. These do **not** create a trace; they only sync the full conversation transcript to the remote service (when storage is remote and the transcript path is local). This keeps conversation content up to date instead of capturing it mid-turn during tool use.

### Cursor — `.cursor/hooks.json`

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [{ "command": "agent-trace record" }],
    "sessionEnd": [{ "command": "agent-trace record" }],
    "afterFileEdit": [{ "command": "agent-trace record" }],
    "afterTabFileEdit": [{ "command": "agent-trace record" }],
    "afterShellExecution": [{ "command": "agent-trace record" }],
    "afterAgentResponse": [{ "command": "agent-trace record" }]
  }
}
```

- **Trace events:** `sessionStart`, `sessionEnd`, `afterFileEdit`, `afterTabFileEdit`, `afterShellExecution`
- **Conversation sync only:** `afterAgentResponse` (no trace; syncs full transcript in remote mode)

### Claude Code — `.claude/settings.json`

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "agent-trace record" }] }],
    "SessionEnd": [{ "hooks": [{ "type": "command", "command": "agent-trace record" }] }],
    "PostToolUse": [
      { "matcher": "Write|Edit", "hooks": [{ "type": "command", "command": "agent-trace record" }] },
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "agent-trace record" }] }
    ],
    "Stop": [{ "hooks": [{ "type": "command", "command": "agent-trace record" }] }]
  }
}
```

- **Trace events:** `SessionStart`, `SessionEnd`, `PostToolUse` (Write/Edit, Bash)
- **Conversation sync only:** `Stop` (no trace; syncs full transcript in remote mode when the agent loop ends)

Existing hooks are **preserved** — agent-trace entries are merged in without overwriting anything.

---

## File structure

```
~/.agent-trace/
  .env                         # service URL config (from .env.example)
  bin/agent-trace              # executable (on PATH)
  lib/agent_trace/             # Python source
    __init__.py
    cli.py                     # CLI commands (argparse)
    config.py                  # Global + project config management
    hooks.py                   # Cursor & Claude Code hook setup
    record.py                  # Trace recording (local JSONL / remote HTTP)
    trace.py                   # Trace record construction
    blame.py                   # AI blame / attribution (local + remote)
    commit_link.py             # Commit-to-trace linking (git hook)
  config.json                  # global config (auth_token)

<your-project>/
  .agent-trace/
    config.json                # project config (storage, project_id)
    traces.jsonl               # local traces (when storage=local)
    commit-links.jsonl         # commit → trace links (when storage=local; used by blame)
  .cursor/hooks.json           # Cursor hooks
  .claude/settings.json        # Claude Code hooks
  .git/hooks/post-commit       # optional: calls agent-trace commit-link
```

## License

Licensed under the [Apache License 2.0](LICENSE).
