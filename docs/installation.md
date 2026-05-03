# Installation

agent-trace ships as a **Python package layout** installed by a **bash installer** into `~/.agent-trace/` by default. There is **no PyPI `pip install agent-trace`** requirement documented here; the supported paths are the **curl one-liner** or **clone + `install.sh`**.

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

## Install from GitHub (one-liner)

```bash
curl -fsSL https://raw.githubusercontent.com/ujjalsharma100/agent-trace-cli/main/install.sh | bash
```

What this does at a high level:

1. If no source tree is present, **downloads** the repository archive from GitHub and re-runs the installer from the extracted tree.
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
| `AGENT_TRACE_INSTALL_BRANCH` | Git branch to download when using the curl bootstrap (default: `main`). |
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

Re-run **`install.sh`** from the latest `main` (curl or clone). The installer overwrites library and viewer files under the install prefix. Your **`project-config.json`** and JSONL data under `projects/<id>/` are not removed by the installer itself.

---

## Next steps

- [Getting started](getting-started.md) — `init`, hooks, first `blame`.
- [Command reference](reference/index.md) — full CLI surface.
