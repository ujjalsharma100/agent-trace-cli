# Concepts — Remotes & sync

A **remote** is a named pointer to an `agent-trace-service` deployment plus the `<org>/<project>` slug that identifies your team's bucket within it. The model is deliberately git-shaped:

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo --token $AT_TOKEN
```

The URL always carries the `<org_slug>/<project_slug>` path. Two shapes are accepted:

```
# Standalone service
<scheme>://<host>[:port]/<org_slug>/<project_slug>

# Behind a shared API gateway (note the /at/ prefix)
<scheme>://<host>[:port]/at/<org_slug>/<project_slug>
```

Use the **gateway** form when the service is served under an `/at/` prefix on a host that fronts other APIs; sync requests are then issued under `…/at/<org>/<project>/api/v1/…` instead of `…/api/v1/…`. Either way, the path is what makes the URL self-describing — anyone reading the config can see exactly which org and project the remote points at, without consulting external docs.

Bare-host URLs (e.g. `https://traces.acme.com`) are rejected, and `/at/` is the **only** accepted extra path segment — any other deeper path (`…/acme/myrepo/extra`) is rejected too.

---

## Nothing is automatic

Unlike some sync clients, agent-trace **does not** upload traces on every save or commit. You run:

- **`agent-trace push`** — upload local changes.
- **`agent-trace pull`** — download remote changes.
- **`agent-trace sync`** — push then pull (convenience).

This keeps local work private by default and makes air-gapped or compliance-heavy workflows straightforward.

---

## Project registration is explicit

Before the first push, the project must exist on the server:

```
agent-trace project create https://traces.acme.com/acme/myrepo --token "$AT_TOKEN"
```

Or fold it into `remote add`:

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo \
    --token "$AT_TOKEN" --create
```

The service returns `404 project_not_found` if you try to sync against an unregistered slug. See the [projects-create reference](../reference/projects-create.md) for token scope details.

---

## Default remote selection

Each project stores a `remote.default` name in `project-config.json`. Commands that accept `--remote` fall back to that default when you omit the flag (and may apply additional "auto" behavior documented on [push/pull/sync](../reference/push-pull-sync.md)).

If only one remote is configured it is the default automatically. If multiple are configured and none is set explicit-default, the CLI prefers `origin`.

---

## Authentication

Tokens are bound **per remote**. The URL and the secret are stored apart, so `remotes.json` can be inspected freely:

- **`--token <value>`** — raw secret. Written to `~/.agent-trace/config.json` under `tokens.<project_id>::<remote_name>` (mode `0o600`). `remotes.json` stores only a reference (`global:<project_id>::<remote_name>`), never the raw value.
- **`--token-env <VAR>`** — only the variable name is persisted. `os.environ[VAR]` is read at every push/pull.

There is no `AGENT_TRACE_TOKEN` / `set globaluser` override that displaces the per-remote binding. The sync code path resolves the token strictly through the remote's `token_ref`. The legacy global `auth_token` slot survives for backward compatibility but does not feed into push/pull/sync. See [push/pull/sync — Authentication](../reference/push-pull-sync.md#authentication) for the full resolution table.

Before every push (and during `doctor`, `remote add`, and `project create`), the CLI calls `GET /api/v1/auth/whoami` on the service and refuses to proceed when the token's resolved org or project scope doesn't match the URL — the "data went somewhere else" failure mode is caught up front.

`remote show <name>` prints URL components (host / org / project) and a masked token preview.

---

## What gets synchronized

High-level artifact families: **traces**, **ledgers**, **commit-links**, **conversations**. `push --full` includes unattributed traces (those not yet referenced by any ledger); plain `push` skips them. Conversations are content-addressed via SHA-256, so the same transcript shared across multiple traces uploads once. Exact wire semantics live in [push/pull/sync](../reference/push-pull-sync.md) and `sync.py` source.

---

## Two checkouts, one project

The whole point of the URL-as-canonical-id model: Alice and Bob both run

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo --token $AT_TOKEN
```

on different machines. Their **local** project ids differ (each has its own anchor uuid in `.git/agent-trace-id`), but the wire `project_id` is the same `myrepo` slug. Alice pushes; Bob pulls; Bob's `agent-trace blame` and `agent-trace context` resolve Alice's lines because the trace bodies and ledger references are now in his local data dir.

If their tokens belong to different orgs, isolation holds — each org has its own `myrepo`.

---

## Operational tip

Use `agent-trace status` and `doctor` to see whether remotes are configured, whether their URLs parse, whether auth resolves, and whether there is **unpushed** local data waiting for an explicit `push`.
