# Commands: `push`, `pull`, `sync`

Explicit **HTTP synchronization** between local JSONL-backed storage and a configured **remote** service. Nothing runs automatically on save or commit.

**Prerequisites:**

1. An **initialized** project (`agent-trace init`).
2. At least one **registered remote** (`agent-trace remote add <name> <url> ...`).
3. The project slug embedded in the remote URL must exist on the server — register it once with `agent-trace project create <url>` or `remote add --create`. See [project create](projects-create.md) and the [project identity](../concepts/project-identity.md) concept.

URLs follow the **slug grammar** enforced both client- and server-side:

```
<scheme>://<host>[:port]/<org_slug>/<project_slug>
```

Bare-host URLs (e.g. `https://traces.acme.com`) are rejected with a clear error and a pointer at the grammar. `org_slug` and `project_slug` each match `^[a-z0-9][a-z0-9._-]{0,63}$`.

---

## `push` {#push}

```text
agent-trace push [OPTIONS]
```

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--remote`** | string | default remote | Which named remote to target. Falls back to `remote.default` or, when only one remote exists, that remote. |
| **`--full`** | flag | off | Include traces **without** ledger attribution. Plain `push` skips unattributed traces; `--full` ships everything. |
| **`--only`** | choice | all | Restrict to one artifact family: `traces`, `ledgers`, or `commit-links`. Conversations and summaries follow trace pushes implicitly. |
| **`--since`** | string | none | ISO-8601 timestamp lower bound on item timestamps. Useful for one-off backfills, independent of the synced-manifest cursor. |
| **`--dry-run`** | flag | off | Compute the plan / counts without performing mutating HTTP writes. |

**Typical use:**

```bash
agent-trace push
agent-trace push --remote team
agent-trace push --full
agent-trace push --only ledgers --dry-run
```

**What gets pushed (in order, idempotent):**

1. **Traces** — bulk POST to `/api/v1/sync/traces`, batched at 500 items per request.
2. **Ledgers** — `/api/v1/sync/ledgers`.
3. **Commit links** — `/api/v1/sync/commit-links`.
4. **Conversations** — content-addressed blobs (see below) plus pointer rows on `/api/v1/sync/conversations`.
5. **Summaries** — `/api/v1/sync/summaries` (silently skipped if the server is too old to expose the endpoint).

Idempotency is enforced by a per-remote manifest in `sync-state.json` (`synced.trace_ids`, `synced.ledger_shas`, …). Re-running `push` against a clean state pushes zero items.

**Exit:** `0` on success. Non-zero on auth, network, scope, or validation failures.

---

## `pull` {#pull}

```text
agent-trace pull [OPTIONS]
```

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--remote`** | string | default | Remote to read from. |
| **`--since`** | string | manifest cursor | Override the per-resource cursor stored in `sync-state.json`. |
| **`--dry-run`** | flag | off | Show what would be merged without writing local JSONL. |

```bash
agent-trace pull
agent-trace pull --since 2026-01-01T00:00:00Z
```

Pull paginates each resource (server cap 1000 items per page, client requests 500) and dedupes by content id against the local store. Receiving the same row twice is a no-op.

---

## `sync` {#sync}

```text
agent-trace sync [--remote NAME]
```

**Purpose:** `push` then `pull` in one invocation for everyday meet-in-the-middle workflows.

| Option | Default | Purpose |
|--------|---------|---------|
| **`--remote`** | default | Target remote override. |

`sync` does **not** expose every `push`/`pull` flag. If you need `--full` or `--only`, run `push` explicitly.

---

## Pre-flight scope check

Before any push (and during `doctor`, `remote add`, and `project create`) the CLI calls **`GET /api/v1/auth/whoami`** on the remote and refuses to proceed when the token's resolved scope doesn't match the URL:

- `org_slug_mismatch` — the token belongs to a different org than the URL's `<org_slug>`.
- `project_scope_mismatch` — a project-scoped token used against a URL that points at a different project slug.
- `whoami_unsupported` — the server is too old to expose `/auth/whoami`; the CLI prints a warning and continues.

This stops the "data went somewhere else" failure mode: typing the wrong slug surfaces immediately rather than silently writing under the token's org.

---

## Authentication

Tokens are bound **per remote**. The `remote add` flow stores a `token_ref` in `remotes.json` next to the URL; the actual secret lives outside the project directory.

### Token reference schemes

| `token_ref` form | Storage | Resolution |
|------------------|---------|-----------|
| `global:<project_id>::<remote_name>` | `~/.agent-trace/config.json` under `tokens.<key>` (mode `0o600`) | `--token <STR>` on `remote add` / `remote set-token` writes the raw value here. |
| `env:<VAR>` | not persisted | `--token-env VAR` records only the variable name. `os.environ[VAR]` is read at every push/pull. |
| `keychain:<name>` | OS keychain | Scaffold only — not wired up in M0. |
| `(no auth)` | n/a | Remote has no `auth` block. Push will fail at the server. |

### Precedence

There is **no environment override** that displaces the per-remote `token_ref`. Push, pull, sync, doctor, project-create, and remote-add all read the token they were configured with. Specifically:

- `AGENT_TRACE_TOKEN` is **not** consulted by `push` / `pull` / `sync` in M0. It survives as a legacy fallback for older `set globaluser`-driven scripts; new code should never depend on it for HTTP auth.
- `AGENT_TRACE_ADMIN_SECRET` is consulted only by `project create` and `remote add --create` — never by sync.
- `set globaluser <token>` / `global.auth-token` write to a global slot that the sync code path no longer reads. They remain for backward compatibility with very old configs. Migrate by reissuing the token to the matching remote with `agent-trace remote set-token <name> --token …`.

### Examples

Bind a remote with a raw token (stored in global config under a per-project key):

```bash
agent-trace remote add origin https://traces.acme.com/acme/myrepo \
    --token "$AT_TOKEN"
```

Bind without persisting the raw value (CI-friendly):

```bash
agent-trace remote add ci https://traces.acme.com/acme/myrepo \
    --token-env AT_TOKEN
```

Rotate a token without losing the URL:

```bash
agent-trace remote set-token origin --token "$NEW_AT_TOKEN"
```

Inspect (token is masked):

```bash
agent-trace remote show origin
```

---

## Chunked conversation upload

Transcripts above **256 KiB** automatically go through a content-addressed blob path so the same content uploads once even when many traces reference it.

The push leg, for each conversation pointer:

1. Computes the SHA-256 of the on-disk transcript and checks the local cache (`projects/<id>/conversations/<sha[:2]>/<sha>`).
2. Calls **`HEAD /api/v1/blobs/<sha>`** to see if the server already has the bytes.
3. If not, streams the bytes to **`POST /api/v1/blobs`** (subject to the service's `BLOB_MAX_BYTES`, default 10 MiB).
4. Sends the conversation pointer row (`conversation_id`, `content_sha256`, `size`) via `/api/v1/sync/conversations` — no inline body.

Small transcripts (below the 256 KiB threshold) ship inline on the conversations sync route and skip the blob endpoints.

If the server does not implement `/api/v1/blobs` (older deployment), the CLI falls back to inline upload on the conversations route. The fallback is logged via push errors so the operator can decide to upgrade the service.

Pull mirrors the same path: small content arrives inline; large content arrives as a sha pointer and the CLI fetches the bytes from `GET /api/v1/blobs/<sha>` into the local cache. After pull, content reads (`agent-trace context`) yield byte-identical transcripts on both machines.

---

## Operational visibility

- `agent-trace status` — surfaces whether unpushed data exists (high-level counts).
- `agent-trace doctor` — confirms every remote URL parses, the server is reachable, and the bound token's whoami matches the URL's `<org>/<project>` slugs.
- `agent-trace push --dry-run` — preflight without side effects.

---

## Related

- [remote](remote.md) — managing named remotes.
- [project create](projects-create.md) — server-side registration.
- [Remotes & sync concept](../concepts/remotes-and-sync.md).
- [Project identity](../concepts/project-identity.md) — local anchor vs. wire slug.
- [Environment variables](../environment-variables.md).
