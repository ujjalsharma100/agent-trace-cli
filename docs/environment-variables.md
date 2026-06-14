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

**Legacy.** Predates per-remote token storage. `push` / `pull` / `sync` resolve the bearer token from the bound remote's `token_ref` (`global:<key>` / `env:<VAR>`), not from this variable.

It remains read-able by the legacy `get_auth_token()` helper for backward compatibility, but nothing in the current sync code path consults it. New configurations should bind tokens with `agent-trace remote add --token` or `--token-env`.

See [push/pull/sync — Authentication](reference/push-pull-sync.md#authentication) for the resolution model used today.

---

## `AGENT_TRACE_ADMIN_SECRET`

Used **only** by project-administration commands when no bearer token is supplied:

- `agent-trace project create <url>` (no `--token` / `--token-env`)
- `agent-trace remote add <name> <url> --create` (no `--token` / `--token-env`)

When set, the CLI sends `X-Admin-Secret: $AGENT_TRACE_ADMIN_SECRET` on `POST /api/v1/projects`. The value must match the service's `ADMIN_SECRET`. Project rows then land in the service's well-known **default org** (`00000000-0000-0000-0000-000000000001`) unless the body specifies otherwise.

Never consulted by `push` / `pull` / `sync` — those always need an org-scoped or project-scoped bearer token.

---

## `AGENT_TRACE_TELEMETRY`

Optional override for **anonymous CLI telemetry** (see [Telemetry](concepts/telemetry.md)).

| When set | Behavior |
|----------|----------|
| Truthy (`1`, `true`, `yes`, `on`, …) | Force telemetry **on** (still fail-closed if POST fails) |
| Falsy (`0`, `false`, `no`, `off`, …) | Force telemetry **off** |
| Unset | Follow **`telemetry.enabled`** in global `config.json` |

---

## Hook / GUI `PATH` augmentation (internal)

Summary **preset** execution augments `PATH` with common install locations (for example `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`) so binaries like `ollama` or `cursor` remain discoverable when hooks run under a reduced environment. This is not a user-set variable but explains “works in terminal, fails in IDE” class issues.

---

## Install-only variables

See [Installation — Installer environment variables](installation.md#installer-environment-variables): `AGENT_TRACE_INSTALL_BRANCH`, `AGENT_TRACE_INSTALL_FROM_GITHUB`, `AGENT_TRACE_INSTALL_TMPDIR`.
