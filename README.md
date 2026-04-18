# agent-trace CLI

A command-line tool for tracing AI-generated code changes across coding agents like **Cursor** and **Claude Code**. Includes the **file viewer** for browsing files with git + agent-trace blame in your browser.

This implementation follows the [Agent Trace](https://agent-trace.dev/) specification and the **redesign** described in the umbrella workspace: deterministic-only attribution, local-first storage, **git-like** `push` / `pull` / `sync`, **git notes** (`refs/notes/agent-trace`) for sharing metadata with the repo, and an optional HTTP remote as a **pure datastore** (no server-side blame).

**How it behaves:**

- **Local-first** — Hooks write to JSONL under `AGENT_TRACE_HOME` (default `~/.agent-trace/`). The repo only contains a small **`.agent-trace/project.json`** pointer (stable `project_id`). Data lives in `~/.agent-trace/projects/<project_id>/`.
- **Optional remote** — Configure a named remote (`agent-trace remote`) and run **`agent-trace push` / `pull` / `sync`** when you want to mirror traces, ledgers, commit-links, and conversations to a remote repository storing the traces. Nothing syncs automatically during editing.
- **Git notes** — `agent-trace notes …` attaches composable JSON to commits so attribution can travel with `git fetch` without any service.

Use **`agent-trace blame <file>`** for per-line **AI**, **HUMAN**, **MIXED**, or **UNKNOWN** labels from the ledger (and git note inline ledger when present). There is **no heuristic blame path**: if there is no ledger (and no usable git note), lines are **UNKNOWN**.

**Zero external dependencies** — uses only the Python standard library (requires Python 3.9+).

---

## Attribution ledger

The **deterministic attribution ledger** is built at **commit time** by the post-commit hook. Each changed line is classified by comparing committed line content (SHA-256 per line) against trace line hashes — not by scoring or probabilities at blame time.

1. **Per-line content hashing** — Traces record hashes for touched lines so matching survives inserts and reordering within reason.
2. **Session edit sequence** — Resolves “last writer wins” when multiple traces touch the same line.
3. **Post-commit hook** — Runs `agent-trace commit-link`: links the commit to traces and appends a ledger for that commit.
4. **Cross-file matching** — The ledger builder can match hashes across files (e.g. moves/refactors) when appropriate.
5. **Post-rewrite hook** — After rebase or amend, `agent-trace rewrite-ledger` remaps ledger commit SHAs.

`agent-trace blame` uses the ledger only. Missing ledger → **UNKNOWN** (honest absence of proof, not a guess).

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
6. Installs the **file viewer** to `~/.agent-trace/viewer/` and creates `~/.agent-trace/bin/agent-trace-viewer`
   - If `npm` is available, builds the frontend from source; otherwise uses the pre-built `dist/`
7. Adds `~/.agent-trace/bin` to your shell PATH (zsh, bash, or fish)
8. **Offers to set up global hooks** for Cursor and Claude Code (optional, per-tool prompt)

After installing, restart your shell (or `source ~/.zshrc`) and verify:

```bash
agent-trace --version
```

### Uninstall

```bash
rm -rf ~/.agent-trace/bin ~/.agent-trace/lib ~/.agent-trace/viewer
```

Then remove the `# agent-trace` + `export PATH=...` lines from your `~/.zshrc` / `~/.bashrc`.

---

## Commands

### `agent-trace init`

Initialize tracing for the current project (interactive): project label, optional **remote** URL and token, **git notes** (refspecs + optional sections), **hooks** (Cursor, Claude Code, git post-commit / post-rewrite), and optional **summary** command for note enrichment.

If **global hooks** are already configured for a tool (via `agent-trace hooks setup-global`), the init wizard skips the project-level hook prompt for that tool — global hooks make project-level hooks redundant.

```bash
cd my-project
agent-trace init
```

### `agent-trace status`

Show project id, storage paths, counts, hook status, remote/sync-related status, and whether unpushed data exists (git-style overview).

```bash
agent-trace status
```

### `agent-trace doctor`

Verify hooks, config, storage, remotes, and optional tools (e.g. summary command).

```bash
agent-trace doctor
```

### `agent-trace reset`

Re-prompts for all settings (storage mode, project ID, auth token, hooks).

```bash
agent-trace reset
```

### `agent-trace hooks {setup-global,remove-global,status}`

Manage **global hooks** for coding tools. Global hooks are installed once in your home directory and fire for every project — like `git config --global`. Edits in an initialised repo are traced; edits elsewhere are silently ignored.

This is the recommended setup: install global hooks once, then `agent-trace init` in each repo you want to trace (for project config, git hooks, and git notes). No per-project Cursor/Claude hook configuration needed.

```bash
# Install global hooks for all supported tools
agent-trace hooks setup-global

# Install for a specific tool only
agent-trace hooks setup-global --tool cursor
agent-trace hooks setup-global --tool claude

# Check current status
agent-trace hooks status

# Remove global hooks
agent-trace hooks remove-global
agent-trace hooks remove-global --tool claude
```

| Subcommand | Options | Description |
|------------|---------|-------------|
| `setup-global` | `--tool cursor\|claude` | Install global hooks (default: all tools) |
| `remove-global` | `--tool cursor\|claude` | Remove global hooks (default: all tools) |
| `status` | — | Show whether global hooks are configured |

**Where hooks are written:**
- Cursor: `~/.cursor/hooks.json`
- Claude Code: `~/.claude/settings.json`

### `agent-trace record`

Record a trace from stdin. This is what the hooks call — you don't run this manually.

```bash
echo '{"hook_event_name":"sessionStart",...}' | agent-trace record
```

### `agent-trace commit-link`

Link the current git commit to the traces that were active in this session. Called automatically by the post-commit hook when you have configured git hooks. Also builds an **attribution ledger** for the commit — a deterministic per-line map of which lines are AI-authored, human-authored, or mixed.

```bash
agent-trace commit-link
```

### `agent-trace rewrite-ledger`

Remap ledger commit SHAs after `git rebase` or `git commit --amend`. Called automatically by the post-rewrite hook — you don't normally run this manually. Git provides old-SHA/new-SHA pairs on stdin; this command updates `.agent-trace/ledgers.jsonl` accordingly.

```bash
# Called by .git/hooks/post-rewrite — not typically run manually
agent-trace rewrite-ledger
```

### `agent-trace viewer [--project /path]`

Open the **file viewer** in your browser. The viewer lets you browse the project's file tree, view file contents, and see git blame and agent-trace blame inline.

The viewer is installed automatically by `install.sh`. If it's missing, re-run `install.sh` to reinstall. Once launched, open **http://127.0.0.1:8765** in your browser.

```bash
agent-trace viewer
agent-trace viewer --project /path/to/repo
```

### `agent-trace context <file>`

Get **conversation context** for AI-attributed lines in a file. Builds on `agent-trace blame` — runs attribution first, then resolves the conversation transcript behind each AI-attributed segment. Two modes:

- **Default** — returns attribution metadata plus a short preview (~200 chars) and conversation size stats (characters, lines, turns). Light enough to use inline.
- **Full (`--full`)** — adds the complete conversation transcript for each AI-attributed segment.

```bash
agent-trace context src/utils/parser.ts
agent-trace context src/utils/parser.ts --lines 10-50
agent-trace context src/utils/parser.ts --lines 10-50 --full
agent-trace context src/utils/parser.ts --json
agent-trace context src/utils/parser.ts --lines 10-50 --query "why was this approach chosen?"
```

| Option | Short | Description |
|--------|--------|-------------|
| `--lines` | `-l` | Line range to focus on (e.g. `10-50`) |
| `--full` | | Include full conversation transcript in output |
| `--json` | | Output as JSON (for machine / subagent consumption) |
| `--query` | `-q` | Pass a query through to the output (for subagent instruction forwarding) |

The JSON output includes per-segment fields: `start_line`, `end_line`, `attribution` (`ai`/`mixed`/`human`), `model_id`, `tool`, `trace_id`, `confidence`, `conversation_url`, `conversation_size`, and `preview`. When `--full` is set, `conversation_content` is also included.

Conversation content is resolved from local `file://` paths (local mode) or fetched from the remote service (remote mode).

---

### `agent-trace rule {add,remove,show,list}`

Manage **prebuilt rules** that teach coding agents (Cursor, Claude Code) how to use agent-trace features. Rules are written as `.mdc` (Cursor) or `.md` (Claude Code) files in the project's rules directory.

```bash
# List available prebuilt rules
agent-trace rule list

# Add a rule for a tool
agent-trace rule add context-for-agents --tool claude
agent-trace rule add context-for-agents --tool cursor

# Show which rules are currently configured
agent-trace rule show

# Remove a rule
agent-trace rule remove context-for-agents --tool claude
```

| Subcommand | Options | Description |
|------------|---------|-------------|
| `list` | — | List all available prebuilt rules with descriptions |
| `add <name>` | `--tool cursor\|claude` | Write the rule file for the given tool |
| `remove <name>` | `--tool cursor\|claude` | Remove the rule file |
| `show` | — | Show all active agent-trace rules in the project |

**Available rules:**

| Name | Description |
|------|-------------|
| `context-for-agents` | Teaches the agent to retrieve conversation context behind AI-attributed code using `agent-trace context` |

Rules are written to:
- Cursor: `.cursor/rules/agent-trace-<name>.mdc`
- Claude Code: `.claude/rules/agent-trace-<name>.md`

---

### `agent-trace blame <file>`

Show **AI attribution** for a file using **deterministic, ledger-only** logic:

- **Ledger** — For each commit, if `.agent-trace/ledgers.jsonl` contains a ledger (built at commit time from traces and line hashes), lines are labelled **AI**, **HUMAN**, or **MIXED** according to that ledger.
- **UNKNOWN** — If there is no ledger for the introducing commit, or a line range is not covered by the ledger, the output is **UNKNOWN** (nothing is inferred from heuristics or scoring).

The command runs `git blame --porcelain`, groups lines by commit, then resolves attribution from the ledger only. Traces in `.agent-trace/traces.jsonl` are used to enrich model and tool metadata when a `trace_id` is present in the ledger.

```bash
agent-trace blame src/utils/parser.ts
agent-trace blame src/utils/parser.ts --line 42
agent-trace blame src/utils/parser.ts --range 10-100
agent-trace blame src/utils/parser.ts --json
agent-trace blame src/utils/parser.ts --show-unknown    # Include UNKNOWN ranges in output
agent-trace blame src/utils/parser.ts --require-attribution   # Exit 1 if any line is UNKNOWN (CI)
```

| Option | Short | Description |
|--------|--------|-------------|
| `--line` | `-l` | Blame a single line |
| `--range` | `-r` | Blame a line range (e.g. `10-25`) |
| `--json` | | Output attributions as JSON (`kind`: `AI`, `HUMAN`, `MIXED`, `UNKNOWN`) |
| `--show-unknown` | | List UNKNOWN ranges (default is to omit them from text output) |
| `--require-attribution` | | Fail with non-zero exit if any line would be UNKNOWN |

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

### `agent-trace remote` — `add` | `list` | `show` | `set-url` | `set-token` | `remove` | `rename` | `default`

Manage named HTTP remotes (like `git remote`). Defaults are used by `push` / `pull` / `sync`.

### `agent-trace push` | `pull` | `sync`

Explicit sync with the configured service: upload/download traces, ledgers, commit-links, and conversations (see `agent-trace push --help` for options such as attributed-only vs full scope).

### `agent-trace notes` — `show` | `attach` | `rebuild` | `backfill` | `strip` | `push` | `pull`

Build and manage JSON under **`refs/notes/agent-trace`** so teammates can receive trace pointers and optional inline ledgers via normal git fetch.

### `agent-trace summary` — `enable` | `disable` | `generate` | `show`

Optional pluggable summaries (user-defined command) for session or commit note sections.

### `agent-trace projects` | `adopt`

List registered projects or adopt a repo directory and print its `project_id`.

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

### In-repo pointer — `.agent-trace/project.json`

Checked in (small file). Contains **`project_id`** so the CLI resolves `~/.agent-trace/projects/<project_id>/`. May include notes-related fields depending on init.

### Project settings — `AGENT_TRACE_HOME/projects/<project_id>/project-config.json`

Created/managed by `agent-trace init`. Holds storage mode, optional service URL and token, `label`, `notes.*`, `summary.*`, remotes, etc. **Do not commit** this file; it stays under `AGENT_TRACE_HOME`.

### Resolution order

| Setting | Priority |
|---------|----------|
| Auth token | `AGENT_TRACE_TOKEN` env > global config > project config |
| Service URL | `AGENT_TRACE_URL` env / `.env` > project config > default (`http://localhost:5000`) |

---

## How hooks work

Hooks pipe coding agent events through `agent-trace record`. They can be installed at two levels:

- **Global** (recommended) — `~/.cursor/hooks.json`, `~/.claude/settings.json`. Fire for *every* project, like `git config --global`. Set up once with `agent-trace hooks setup-global`.
- **Project-level** — `<project>/.cursor/hooks.json`, `<project>/.claude/settings.json`. Set up per-project during `agent-trace init`.

Global hooks are the recommended approach. The recording pipeline resolves the correct project from the **file being edited** (via its git root), not from the agent's working directory. This means:

- Edits to files inside an initialised repo are recorded for that project
- Edits to files outside any initialised repo are silently ignored
- An agent running from a parent directory editing files in a subfolder project works correctly
- An agent running from a subfolder of an initialised project works correctly

When global hooks are present, `agent-trace init` skips the per-tool hook prompts — they'd be redundant.

There are two kinds of hook events:

1. **Trace-recording hooks** — after file edits, shell runs, and session start/end. Each event produces a trace record (stored locally or sent to the remote service). Traces include per-line content hashes and edit sequence numbers for deterministic attribution.
2. **Conversation-sync hooks** — after the assistant has finished a full response. These do **not** create a trace; they only sync the full conversation transcript to the remote service (when storage is remote and the transcript path is local). This keeps conversation content up to date instead of capturing it mid-turn during tool use.

### Git hooks

Two git hooks are installed when you configure git hooks during `agent-trace init`:

- **`post-commit`** — Runs `agent-trace commit-link` after every commit. This links the commit to its traces and builds the attribution ledger.
- **`post-rewrite`** — Runs `agent-trace rewrite-ledger` after rebase or amend. This remaps ledger SHAs from old commits to their new counterparts.

### Cursor — `.cursor/hooks.json` (project) or `~/.cursor/hooks.json` (global)

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

### Claude Code — `.claude/settings.json` (project) or `~/.claude/settings.json` (global)

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
~/.cursor/hooks.json             # Cursor global hooks (optional, via hooks setup-global)
~/.claude/settings.json          # Claude Code global hooks (optional, via hooks setup-global)

~/.agent-trace/
  .env                           # default service URL (from .env.example)
  bin/agent-trace                # CLI executable (on PATH)
  bin/agent-trace-viewer         # viewer launcher (on PATH)
  lib/agent_trace/               # Python source
    __init__.py
    cli.py                       # CLI commands (argparse)
    config.py                    # Global + project resolution (project.json → project_id)
    hooks.py                     # Cursor, Claude Code & git hook setup (project + global)
    record.py                    # Trace recording from hooks
    trace.py                     # Trace record construction + per-line hashing
    blame.py                     # Deterministic blame (ledger + git notes; UNKNOWN when missing)
    context.py                   # Conversation context for AI-attributed segments
    rules.py                     # Prebuilt agent rules (Cursor, Claude Code)
    commit_link.py               # Commit-link + ledger build (post-commit)
    ledger.py                    # Ledger construction
    rewrite.py                   # Post-rewrite SHA remapping
    sync.py                      # Push/pull/sync to HTTP remote
    git_notes.py                 # Git notes (refs/notes/agent-trace)
    remote.py                    # Named remotes
    ...
  viewer/                        # file viewer (installed by install.sh)
  config.json                    # global config (auth_token)

~/.agent-trace/projects/<project_id>/
  project-config.json            # project settings (not in git)
  traces.jsonl
  commit-links.jsonl
  ledgers.jsonl
  session-state.json
  sync-state.json                # push/pull cursors (when using remote)

<your-project>/
  .agent-trace/
    project.json                 # checked-in: project_id (and optional note defaults)
  .cursor/hooks.json             # Cursor project hooks (only if no global hooks)
  .claude/settings.json          # Claude Code project hooks (only if no global hooks)
  .git/hooks/post-commit         # agent-trace commit-link
  .git/hooks/post-rewrite        # agent-trace rewrite-ledger
```

## License

Licensed under the [Apache License 2.0](LICENSE).
