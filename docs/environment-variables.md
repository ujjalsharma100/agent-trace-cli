# Environment variables

This page lists environment variables that affect **runtime behavior** of the `agent-trace` CLI and related launchers. Variables only read by **`install.sh`** are documented on the [Installation](installation.md) page.

---

## `AGENT_TRACE_HOME`

**Default:** `~/.agent-trace`

Overrides the root directory for:

- Global `config.json`
- `projects/<id>/` trees (traces, ledgers, project-config, sync state, summaries)
- `projects.json` registry
- Other global auxiliary directories (`sessions/`, `detached/`, …)

**Typical uses:**

- Running tests in isolation.
- Storing trace data on a dedicated volume.
- Per-developer sandboxes on shared machines.

---

## `AGENT_TRACE_TOKEN`

If set to a non-empty string, this token is used for **HTTP remote authentication** in preference to the token stored in global config (`auth_token` from `set globaluser` / `global.auth-token`).

Resolution order (auth): **`AGENT_TRACE_TOKEN` env → global config file**.

---

## Hook / GUI `PATH` augmentation (internal)

Summary **preset** execution augments `PATH` with common install locations (for example `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`) so binaries like `ollama` or `cursor` remain discoverable when hooks run under a reduced environment. This is not a user-set variable but explains “works in terminal, fails in IDE” class issues.

---

## Install-only variables

See [Installation — Installer environment variables](installation.md#installer-environment-variables): `AGENT_TRACE_INSTALL_BRANCH`, `AGENT_TRACE_INSTALL_FROM_GITHUB`, `AGENT_TRACE_INSTALL_TMPDIR`.
