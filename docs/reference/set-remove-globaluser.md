# Commands: `set globaluser`, `remove globaluser`

These are thin **global auth token** helpers stored in **`$AGENT_TRACE_HOME/config.json`** under the key **`auth_token`**. They predate / parallel **`config set global.auth-token`** but remain for ergonomic compatibility.

---

## `set globaluser`

```text
agent-trace set globaluser <token>
```

| Positional | Description |
|------------|-------------|
| **`token`** | Bearer-like secret used for HTTP remote authentication when **`AGENT_TRACE_TOKEN`** is unset. |

**Side effects:** Creates parent dirs if needed; writes JSON; attempts restrictive file permissions where supported.

**Security:** The token appears in **shell history** unless you suppress history — prefer **`config set global.auth-token …`** from a here-doc or secret manager in sensitive environments.

**Exit:** `0`.

---

## `remove globaluser`

```text
agent-trace remove globaluser
```

**Purpose:** Delete **`auth_token`** from global config if present.

**Stdout:** Confirms removal or states that no token was configured.

**Exit:** `0`.

---

## Resolution order (reminder)

1. **`AGENT_TRACE_TOKEN`** environment variable  
2. Global config **`auth_token`**

See [Environment variables](../environment-variables.md).
