# Installation

agent-trace is published on **PyPI** as **`agent-trace-cli`**. Pick the path that fits your setup:

| Path | What you get |
|------|----------------|
| **`pip install agent-trace-cli`** | The `agent-trace` CLI on your PATH (Python 3.9+). Best when you already use pip/venv. |
| **curl one-liner** or **`install.sh`** | Same CLI, plus the **file viewer** under `~/.agent-trace/`, PATH setup, and optional global hook prompts. |

---

## Requirements

| Requirement | Notes |
|-------------|--------|
| **Python** | Version **3.9** or newer (`python3` on PATH). |
| **Git** | Required for repo identity, hooks, notes, and blame. |
| **Shell** | Installer is **bash** (`bash install.sh` or pipe to `bash`). |
| **curl** | Required only when bootstrapping from GitHub without a local clone. |
| **npm** | Optional; used to build the file viewer frontend when present. If absent, the installer can use a pre-built `dist/`. |

---

## Install from PyPI

```bash
pip install agent-trace-cli
```

This installs the **`agent-trace`** console script. Verify:

```bash
agent-trace --version
```

Use a virtualenv if your OS Python is externally managed (for example `python3 -m venv .venv && source .venv/bin/activate` before the install command above).

**File viewer:** `pip install` does **not** bundle the local web viewer. For `agent-trace viewer`, use the [curl one-liner](#install-from-github-one-liner) or [local `install.sh`](#install-from-a-local-clone) below, or run `install.sh` after pip if you only need the viewer assets added under `~/.agent-trace/`.

**Upgrade:** `pip install -U agent-trace-cli`

**Uninstall:** `pip uninstall agent-trace-cli` — this removes the CLI only. Local trace data under `~/.agent-trace/` is unchanged unless you delete it yourself (see [Uninstall](#uninstall)).

---

## Install from GitHub (one-liner)

```bash
curl -fsSL https://raw.githubusercontent.com/ujjalsharma100/agent-trace-cli/main/install.sh | bash
```

What this does at a high level:

1. If no source tree is present, **downloads** the **latest GitHub Release** install tarball (`agent-trace-cli.tar.gz`, uploaded on each version tag) and re-runs the installer from the extracted tree.
2. Verifies **Python 3.9+**.
3. Copies Python sources to **`~/.agent-trace/lib/`** (or `$AGENT_TRACE_HOME/lib/` if overridden).
4. Creates executables under **`~/.agent-trace/bin/`**:
   - `agent-trace` — main CLI
   - `at` — short alias
   - `agent-trace-viewer` — launches the file viewer stack
5. Installs the **file viewer** under `~/.agent-trace/viewer/`.
6. **Appends** a PATH snippet to `~/.zshrc`, `~/.bashrc`, or fish config when appropriate.
7. **Optionally prompts** to set up global hooks for Cursor and Claude Code.

After installation, **restart the shell** or `source` your rc file, then run `agent-trace --version`.

---

## Install from a local clone

```bash
git clone https://github.com/ujjalsharma100/agent-trace-cli
cd agent-trace-cli
bash install.sh
```

Useful when developing the tool or when corporate policy blocks piping scripts from the network.

---

## Installer environment variables

These are read by `install.sh` (not necessarily by every `agent-trace` subcommand):

| Variable | Purpose |
|----------|---------|
| `AGENT_TRACE_INSTALL_VERSION` | Pin a GitHub Release tag when bootstrapping (example: `v0.1.3`). Default: latest release. |
| `AGENT_TRACE_INSTALL_BRANCH` | Dev/testing only — bootstrap from a git branch archive instead of a release. |
| `AGENT_TRACE_INSTALL_FROM_GITHUB` | Internal flag set when the installer re-execs after download. |
| `AGENT_TRACE_INSTALL_TMPDIR` | Temporary directory removed after a curl-based install. |

Runtime variables such as `AGENT_TRACE_HOME` are documented under [Environment variables](environment-variables.md).

---

## Viewer build behavior

The viewer is a small backend plus a **Vite** frontend. When **`npm`** is available, the installer may build the frontend from source; otherwise it relies on a **pre-built `dist/`** shipped in the repository. If the viewer binary fails to start, re-run `install.sh` after installing Node/npm, or open an issue with the error from `agent-trace-viewer`.

Default local URL after launch is typically **`http://127.0.0.1:8765`** (see [viewer](reference/viewer.md)).

---

## Uninstall

Remove the install tree and PATH lines you no longer want:

```bash
rm -rf ~/.agent-trace/bin ~/.agent-trace/lib ~/.agent-trace/viewer
```

Then edit `~/.zshrc` / `~/.bashrc` / fish config and remove the block marked for agent-trace (the installer adds a comment such as `# agent-trace`).

Removing **`~/.agent-trace/`** entirely deletes **all** projects’ traces, ledgers, config, and registry data — only do that if you intend to wipe local state.

---

## Upgrading

- **PyPI install:** `pip install -U agent-trace-cli`
- **Bash installer:** re-run **`install.sh`** from the latest GitHub Release (curl or clone). The installer overwrites library and viewer files under the install prefix. Your **`project-config.json`** and JSONL data under `projects/<id>/` are not removed by the installer itself.

---

## Next steps

- [Getting started](getting-started.md) — `init`, hooks, first `blame`.
- [Command reference](reference/index.md) — full CLI surface.
