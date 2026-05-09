# Telemetry

agent-trace can send **anonymous** usage metrics when you explicitly opt in. Telemetry is **off by default** and must never affect normal CLI behavior: network errors and collector downtime are ignored (`fail closed`).

---

## Enabling and checking status

```bash
agent-trace --telemetry on
agent-trace --telemetry off
agent-trace --telemetry status
```

Do not pass a subcommand on the same line as `--telemetry` (the CLI will error). Preferences are stored under **`$AGENT_TRACE_HOME/config.json`** in a `telemetry` object.

---

## Environment override

Set **`AGENT_TRACE_TELEMETRY`** to force behavior regardless of the config file:

| Value | Effect |
|-------|--------|
| `1`, `true`, `yes`, `on`, … | Force telemetry **on** (still anonymous; still fail-closed on send errors) |
| `0`, `false`, `no`, `off`, … | Force telemetry **off** |
| *(unset)* | Follow the config file (`--telemetry on` / `off`) |

---

## What is sent

When telemetry is effectively **on**, after each **subcommand** finishes the CLI may **POST** a single JSON object to the collector URL (see below). Payload fields:

| Field | Meaning |
|-------|---------|
| **`install_id`** | Random UUID created on first opt-in; ties events to one machine/profile without identifying you |
| **`version`** | CLI version string (same as `agent-trace --version`) |
| **`command`** | Top-level subcommand name (e.g. `blame`, `push`, `notes`) |
| **`exit_code`** | Process exit code for that invocation |
| **`duration_ms`** | Wall-clock duration of the command in milliseconds |

No repository paths, file names, trace content, tokens, or hostnames are included.

---

## Collector URL (M0)

There is **no production telemetry endpoint** in M0. The client uses a **placeholder URL** that cannot accept connections; POST attempts fail harmlessly and are discarded. This lets us ship the **opt-in switch and payload shape** now and turn on a real endpoint later without requiring a CLI upgrade for users who already enabled telemetry.

When a collector is ready, the URL in the client will be updated in a normal release; behavior (payload, fail-closed semantics, env override) stays the same unless documented otherwise.

For details on configuration layout, see [Configuration](../configuration.md#telemetry).
