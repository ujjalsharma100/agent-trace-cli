# Commands: `set globaluser`, `remove globaluser`

> **Legacy.** These commands predate the per-remote token model. They write/clear `auth_token` in `$AGENT_TRACE_HOME/config.json` — a slot that the sync code path **no longer consults** for HTTP authentication. They are retained for backward compatibility with very old configs.
>
> **For new setups, bind tokens per remote with [`agent-trace remote add --token`](remote.md#remote-add) / [`remote set-token`](remote.md#remote-set-token).** See [push/pull/sync — Authentication](push-pull-sync.md#authentication) for the resolution model used today.

---

## `set globaluser`

```text
agent-trace set globaluser <token>
```

| Positional | Description |
|------------|-------------|
| **`token`** | Bearer-like secret written to `$AGENT_TRACE_HOME/config.json` under `auth_token`. |

**Effect today:** Writes the value to disk. Nothing in `push` / `pull` / `sync` / `doctor` reads it. The `agent-trace remote add --token` / `--token-env` path is the only one that wires a token into HTTP requests.

**Side effects:** Creates parent dirs if needed; writes JSON; attempts restrictive file permissions where supported.

**Security:** The token appears in **shell history** unless you suppress history — prefer `agent-trace remote add` from a here-doc or secret manager.

**Exit:** `0`.

---

## `remove globaluser`

```text
agent-trace remove globaluser
```

**Purpose:** Delete `auth_token` from global config if present.

**Stdout:** Confirms removal or states that no token was configured.

**Exit:** `0`.

---

## Migrating off `globaluser`

If you previously relied on `set globaluser` for push/pull, recreate the binding on each remote:

```bash
agent-trace remote set-token origin --token "$AT_TOKEN"
# or, for CI:
agent-trace remote set-token ci --token-env AT_TOKEN
```

Then `agent-trace remove globaluser` to drop the unused global value.

`agent-trace doctor` will report `Remote 'origin' token matches URL` once the per-remote binding is in place — that line is the authoritative signal that auth resolves correctly.

---

## Related

- [`agent-trace remote`](remote.md) — modern per-remote token model.
- [push/pull/sync — Authentication](push-pull-sync.md#authentication) — token-resolution semantics.
- [Environment variables](../environment-variables.md).
