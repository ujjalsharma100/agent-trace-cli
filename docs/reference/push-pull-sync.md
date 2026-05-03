# Commands: `push`, `pull`, `sync`

Explicit **HTTP synchronization** between local JSONL-backed storage and a configured **remote** service. Nothing runs automatically on save or commit.

**Prerequisite:** Initialized project + at least one **`remote add`** (except dry-run listing cases).

---

## `push` {#push}

```text
agent-trace push [OPTIONS]
```

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--remote`** | string | auto / default remote | Which named remote to target. |
| **`--full`** | flag | off | Include traces **without** ledger attribution, not just attributed subset. |
| **`--only`** | choice | all | Restrict to one artifact family: `traces`, `ledgers`, or `commit-links`. |
| **`--since`** | string | none | Push subset newer than timestamp / commit-ish token (string passed to implementation — treat as opaque filter per version docs). |
| **`--dry-run`** | flag | off | Compute the plan / counts without performing mutating HTTP writes. |

**Typical use:**

```bash
agent-trace push
agent-trace push --remote team
agent-trace push --full
agent-trace push --only ledgers --dry-run
```

**Exit:** `0` on success; non-zero on auth / network / validation failures.

---

## `pull` {#pull}

```text
agent-trace pull [OPTIONS]
```

| Option | Type | Default | Purpose |
|--------|------|---------|---------|
| **`--remote`** | string | auto / default | Remote to read from. |
| **`--since`** | string | none | Incremental pull filter (timestamp-oriented in typical deployments). |
| **`--dry-run`** | flag | off | Show what would be merged without writing local JSONL. |

```bash
agent-trace pull
agent-trace pull --since 2026-01-01T00:00:00Z
```

---

## `sync` {#sync}

```text
agent-trace sync [--remote NAME]
```

**Purpose:** **`push`** then **`pull`** in one invocation for everyday “meet in the middle” workflows.

| Option | Default | Purpose |
|--------|---------|---------|
| **`--remote`** | auto | Target remote override. |

Does **not** currently expose every `push`/`pull` flag — if you need `--full` or `--only`, run **`push`** explicitly.

---

## Auth reminder

Resolve tokens via **`AGENT_TRACE_TOKEN`** first, else global config / remote-specific refs. See [Environment variables](../environment-variables.md).

---

## Operational visibility

`agent-trace status` surfaces whether unpushed data exists (high-level). Use **`--dry-run`** on `push` for a preflight without side effects.
