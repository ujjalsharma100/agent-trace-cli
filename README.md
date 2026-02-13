# agent-trace CLI

A command-line tool for tracing AI-generated code changes across coding agents like **Cursor** and **Claude Code**.

Works in two modes:
- **Local** — traces saved to `.agent-trace/traces.jsonl` in your project (no server needed)
- **Remote** — traces sent to the [agent-trace-service](../agent-trace-service/) for centralized storage

**Zero external dependencies** — uses only the Python standard library (requires Python 3.9+).

---

## Installation

### From the repo (local install)

```bash
git clone <repo-url>
cd agent-trace
bash agent-trace-cli/install.sh
```

### One-liner (when hosted)

```bash
curl -fsSL https://your-domain.com/install.sh | bash
```

### What the installer does

1. Checks for Python 3.9+
2. Copies Python source to `~/.agent-trace/lib/`
3. Creates an executable at `~/.agent-trace/bin/agent-trace`
4. Copies `.env.example` to `~/.agent-trace/.env` (if no `.env` exists)
5. Adds `~/.agent-trace/bin` to your shell PATH (zsh, bash, or fish)

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
  config.json                  # global config (auth_token)

<your-project>/
  .agent-trace/
    config.json                # project config (storage, project_id)
    traces.jsonl               # local traces (when storage=local)
  .cursor/hooks.json           # Cursor hooks
  .claude/settings.json        # Claude Code hooks
```
