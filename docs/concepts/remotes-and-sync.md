# Concepts — Remotes & sync

Named **remotes** behave similarly to **`git remote`**: each has a **name**, **URL**, and optional **authentication**. They exist so teams can **`push`** and **`pull`** JSONL-backed artifacts to a shared HTTP service **when they choose to**.

---

## Nothing is automatic

Unlike some sync clients, agent-trace **does not** upload traces on every save or commit. You run:

- **`agent-trace push`** — upload local changes.
- **`agent-trace pull`** — download remote changes.
- **`agent-trace sync`** — push then pull (convenience).

This keeps local work private by default and makes air-gapped or compliance-heavy workflows straightforward.

---

## Default remote selection

Each project stores a **`remote.default`** name in `project-config.json`. Commands that accept **`--remote`** fall back to that default when you omit the flag (and may apply additional “auto” behavior documented on [push/pull/sync](../reference/push-pull-sync.md)).

---

## Authentication

Tokens can be supplied when adding or updating a remote (`--token`, `--token-env`). Resolution also honors:

- **`AGENT_TRACE_TOKEN`** environment variable (highest priority for some operations — see [Environment variables](../environment-variables.md)).
- **`global.auth-token`** / `set globaluser` in `~/.agent-trace/config.json`.

`remote show` prints URLs with tokens **masked**.

---

## What gets synchronized

High-level artifact families include **traces**, **ledgers**, **commit-links**, and related conversation payloads depending on flags (for example **`push --full`** includes unattributed traces where **`push`** alone may not). Exact semantics live in the [push/pull/sync reference](../reference/push-pull-sync.md) and in `sync.py` source.

---

## Operational tip

Use **`agent-trace status`** and **`doctor`** to see whether remotes are configured, whether auth resolves, and whether there is **unpushed** local data waiting for an explicit `push`.
