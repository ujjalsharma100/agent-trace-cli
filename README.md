# agent-trace CLI

A command-line tool for tracing AI-generated code changes across coding agents like **Cursor** and **Claude Code**. Includes the **file viewer** for browsing files with git + agent-trace blame in your browser.

This implementation follows the [Agent Trace](https://agent-trace.dev/) specification and the **redesign** described in the umbrella workspace: deterministic-only attribution, local-first storage, **git-like** `push` / `pull` / `sync`, **git notes** (`refs/notes/agent-trace`) for sharing metadata with the repo, and an optional HTTP remote as a **pure datastore** (no server-side blame).

When your host gives a trace **remote URL** and a **Bearer token**, add the remote with **`agent-trace remote add <name> <url> --token`** or **`--token-env`**. URLs may use a gateway path prefix such as **`/at/<org>/<project>`** before **`/api/v1/...`** sync routes.

**How it behaves:**

- **Local-first** — Hooks write JSONL under `AGENT_TRACE_HOME` (default `~/.agent-trace/`). Nothing is written into the repo. The `project_id` is derived from the repo's absolute path (Claude-Code convention: `/Users/jane/myrepo` → `-Users-jane-myrepo`), so data lives at `~/.agent-trace/projects/<project_id>/`.
- **Git-like flow** — `agent-trace init` is zero-prompt and sets up everything locally (like `git init`). Add remotes later with `agent-trace remote add` and run **`agent-trace push` / `pull` / `sync`** explicitly when you want to share. Nothing syncs automatically during editing.
- **Git notes** — `agent-trace notes …` attaches composable JSON to commits under `refs/notes/agent-trace`, so attribution travels with `git fetch` / `git push` once the notes refspec is configured (set up automatically for `origin` during `init`).

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

### pip / Python package

The project is published as a wheel (`pyproject.toml`). From a clone, in a virtualenv:

```bash
pip install .
# contributors (tests + hypothesis + jsonschema):
pip install -e ".[dev]"
```

When released to PyPI: `pip install agent-trace-cli`. Build artifacts locally with `python -m build` after `pip install -e ".[dev]"` (or `pip install build`).

### What the installer does

1. If run via curl, downloads the repo from GitHub and runs the installer
2. Checks for Python 3.9+
3. Copies Python source to `~/.agent-trace/lib/`
4. Creates executables at `~/.agent-trace/bin/agent-trace` and a short alias `~/.agent-trace/bin/at`
5. Installs the **file viewer** to `~/.agent-trace/viewer/` and creates `~/.agent-trace/bin/agent-trace-viewer`
   - If `npm` is available, builds the frontend from source; otherwise uses the pre-built `dist/`
6. Adds `~/.agent-trace/bin` to your shell PATH (zsh, bash, or fish)
7. **Offers to set up global hooks** for Cursor and Claude Code (optional, per-tool prompt)

After installing, restart your shell (or `source ~/.zshrc`) and verify:

```bash
agent-trace --version
at --version        # short alias, same binary
```

### Uninstall

```bash
rm -rf ~/.agent-trace/bin ~/.agent-trace/lib ~/.agent-trace/viewer
```

Then remove the `# agent-trace` + `export PATH=...` lines from your `~/.zshrc` / `~/.bashrc`.

---

## Commands

### `agent-trace init`

Zero-prompt initialization — like `git init`. Writes `project-config.json` for the repo, enables notes (including **all-session conversations** by default), turns on **session summaries** with the built-in **ollama-summary** preset (default model **llama3.1:8b**; requires `ollama` on `PATH` when hooks run), installs git hooks (`post-commit`, `post-rewrite`) and per-tool hooks (Cursor, Claude Code) when a global hook is not already present, and auto-configures the git notes refspec (`refs/notes/agent-trace`) for `origin` if one exists.

No prompts, no remote. If you want to share traces with a team, add a remote later with `agent-trace remote add`. Re-run with `agent-trace reset` to reconfigure interactively.

```bash
cd my-project
agent-trace init      # or: at init
```

### `agent-trace status`

Show project id, data paths, counts, hook status, remote/sync-related status, and whether unpushed data exists (git-style overview).

```bash
agent-trace status
```

### `agent-trace config {show,set,reset}`

Show the full persisted configuration (human-readable field list by default, or `--json`), or update/reset a specific field without going through the full interactive reset flow. Token values are masked in `config show`.

```bash
agent-trace config show
agent-trace config show --json
agent-trace config set notes.include-summary false
agent-trace config reset notes
agent-trace config reset summary.command
agent-trace config reset summary.command --yes
```

`config reset` is interactive by default for the selected field/group; pressing Enter accepts the reset default. Use `--yes` for non-interactive direct reset.

### `agent-trace doctor`

Verify hooks, config, storage, remotes, and optional tools (e.g. summary command).

```bash
agent-trace doctor
```

### `agent-trace reset`

Interactive reconfiguration — prompts for notes sections, summary command, and hook installation. Remotes are managed separately (`agent-trace remote`).

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

Conversation content is resolved from local `file://` paths recorded alongside the trace.

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

Optional pluggable summaries of the agent's conversation transcript. When enabled, agent-trace pipes the raw transcript file (whatever the agent writes to `transcript_path` — Claude Code JSONL, Cursor's equivalent, etc.) on stdin to your configured command. The command's stdout is treated as the summary text and stored keyed by `conversation_url` (`file://<transcript_path>`).

```bash
# Enable: command takes transcript text on stdin, prints summary text on stdout.
agent-trace summary enable --command 'my-summarizer'
agent-trace summary enable --command 'my-summarizer' --timeout 60

# Discover built-in presets (local CLI tools)
agent-trace summary presets

# Configure a built-in preset
agent-trace summary use claude-summary
agent-trace summary use cursor-summary
agent-trace summary use ollama-summary --model llama3.1:8b

# Manually regenerate for one URL or for every URL touched by a session.
agent-trace summary generate --conversation-url 'file:///path/to/transcript.jsonl'
agent-trace summary generate --session-id <conversation_id>

# Inspect summaries attached to a commit.
agent-trace summary show              # HEAD
agent-trace summary show <commit>

agent-trace summary disable
```

The schema of the transcript is opaque to agent-trace — your command decides how to parse it. Storage is `~/.agent-trace/projects/<id>/session-summaries.jsonl`; latest row per `conversation_url` wins. `agent-trace blame` and `agent-trace context` will show the summary in place of the raw transcript preview / URL when one exists.

Built-in preset aliases:

- `claude-summary` → runs `claude -p "<prompt>"`
- `cursor-summary` → runs `cursor agent -p "<prompt>" --trust`
- `ollama-summary` → runs `ollama run <model> "<prompt>"` (`--model` optional; default `llama3.1:8b`)

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

### Project identity — no in-repo file

The `project_id` is derived from the canonical repo path (e.g. `/Users/jane/myrepo` → `-Users-jane-myrepo`). Nothing is written into the repo to identify it; every invocation recomputes the id from the working directory. Moving the repo changes the id — same as `git init`'ing a fresh copy.

### Project settings — `~/.agent-trace/projects/<project_id>/project-config.json`

Created/managed by `agent-trace init`. Holds `notes.*`, `summary.*`, and per-project remote defaults. Lives under `AGENT_TRACE_HOME` — never committed.

### Resolution order

| Setting | Priority |
|---------|----------|
| Auth token | `AGENT_TRACE_TOKEN` env > global config |
| Remote URL | Named remote from `agent-trace remote` (per-project) |

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

1. **Trace-recording hooks** — after file edits, shell runs, and session start/end. Each event produces a trace record (written to JSONL under `AGENT_TRACE_HOME`). Traces include per-line content hashes and edit sequence numbers for deterministic attribution.
2. **Conversation-sync hooks** — after the assistant has finished a full response. These do **not** create a trace; they refresh the local reference to the conversation transcript so later `agent-trace context` calls see the full turn. Sharing with a remote happens explicitly via `agent-trace push` / `sync`.

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
- **Conversation sync only:** `afterAgentResponse` (no trace; refreshes transcript reference)

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
- **Conversation sync only:** `Stop` (no trace; refreshes transcript reference when the agent loop ends)

Existing hooks are **preserved** — agent-trace entries are merged in without overwriting anything.

---

## File structure

```
~/.cursor/hooks.json             # Cursor global hooks (optional, via hooks setup-global)
~/.claude/settings.json          # Claude Code global hooks (optional, via hooks setup-global)

~/.agent-trace/
  bin/agent-trace                # CLI executable (on PATH)
  bin/at                         # short alias → agent-trace
  bin/agent-trace-viewer         # viewer launcher (on PATH)
  lib/agent_trace/               # Python source
    __init__.py
    cli.py                       # CLI commands (argparse)
    config.py                    # Global + project settings
    storage.py                   # Path-based project_id + AGENT_TRACE_HOME paths
    registry.py                  # Optional metadata registry (first commit, origin, known_roots)
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
  projects.json                  # optional metadata registry

~/.agent-trace/projects/<project_id>/     # project_id = sanitized absolute repo path
  project-config.json            # project settings
  traces.jsonl
  commit-links.jsonl
  ledgers.jsonl
  session-state.json
  session-summaries.jsonl
  sync-state.json                # push/pull cursors (when using remotes)

<your-project>/                  # NOTHING is written into the repo itself for identity
  .cursor/hooks.json             # Cursor project hooks (only if no global hooks)
  .claude/settings.json          # Claude Code project hooks (only if no global hooks)
  .git/hooks/post-commit         # agent-trace commit-link
  .git/hooks/post-rewrite        # agent-trace rewrite-ledger
  .git/config                    # notes refspec added for `origin` so notes travel with push/fetch
```

## License

Licensed under the [Apache License 2.0](LICENSE).
