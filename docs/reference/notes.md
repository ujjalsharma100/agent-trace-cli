# Command: `notes`

```text
agent-trace notes <ACTION> ...
```

**Purpose:** Build, inspect, rewrite, shrink, and transport **git notes** on ref **`refs/notes/agent-trace`**. Payloads are JSON documents versioned by schema (see repository `schemas/git-note.schema.json`).

`ACTION` is **required**.

---

## Section flags (shared pattern)

Several actions accept toggles for optional note sections. For each section you may pass **at most one** of **`--include-<section>`** or **`--no-include-<section>`**. If **neither** is passed, the value comes from **project config** (`notes.*` keys — see [Configuration](../configuration.md)).

Passing **both** include and no-include for the same section → **error** on stderr, exit **`1`**.

**Sections:**

| CLI flag pair | Config key (dotted) | Meaning |
|---------------|---------------------|---------|
| `--include-ledger` / `--no-include-ledger` | `notes.include-ledger` | Inline per-line AI segments in the note (larger). |
| `--include-summary` / `--no-include-summary` | `notes.include-summary` | Map of `conversation_url` → summary text. |
| `--include-prompts` / `--no-include-prompts` | `notes.include-prompts` | Array of prompt strings. |
| `--include-all-session-conversations` / `--no-include-all-session-conversations` | `notes.all-session-conversations` | Broader session conversation list (staging window). |

---

## `notes show`

```text
agent-trace notes show [COMMIT]
```

| Positional | Default | Description |
|------------|---------|-------------|
| **`COMMIT`** | `HEAD` | Any revision `git` can resolve. |

**Stdout:** Pretty JSON note if present.

**Exit:** **`1`** if revision invalid or note missing.

---

## `notes attach`

```text
agent-trace notes attach [COMMIT] [SECTION FLAGS...]
```

**Purpose:** Build a fresh note from the **local ledger** for the resolved commit SHA and **attach** it to `refs/notes/agent-trace`.

**Precondition:** A **local ledger** row must exist for that commit (commit after hooks ran). Otherwise stderr explains to commit with hooks enabled.

| Positional | Default | Description |
|------------|---------|-------------|
| **`COMMIT`** | `HEAD` | Target commit. |

**Section flags:** as described in [Section flags](#section-flags-shared-pattern).

**Exit:** `0` on attach success; **`1`** on failure.

---

## `notes rebuild`

```text
agent-trace notes rebuild <range_spec> [SECTION FLAGS...]
```

| Positional | Required | Example | Purpose |
|------------|----------|---------|---------|
| **`range_spec`** | **yes** | `HEAD~10..HEAD` | Passed to `git rev-list` style range processing internally — rebuild every commit in range that has local ledgers. |

**Section flags:** same as `attach`.

**Stdout:** `Rebuilt notes for N commit(s)`.

---

## `notes backfill`

```text
agent-trace notes backfill [--since DATE] [SECTION FLAGS...]
```

| Option | Purpose |
|--------|---------|
| **`--since`** | Forwarded to `git rev-list --since` semantics (example: `2026-01-01`). |

Rebuilds notes for matching commits (implementation selects commits with local ledgers).

---

## `notes strip`

```text
agent-trace notes strip [COMMIT] [--ledger] [--summary] [--prompts] [--all-session-conversations]
```

**Purpose:** Remove optional sections from an **existing** note to reduce size or redact payloads.

| Positional | Default |
|------------|---------|
| **`COMMIT`** | `HEAD` |

| Flag | Section removed |
|------|-----------------|
| **`--ledger`** | `ledger` |
| **`--summary`** | `summary` |
| **`--prompts`** | `prompts` |
| **`--all-session-conversations`** | `all_session_conversations` |

If you pass **no** strip flags, the note is re-attached unchanged (no sections removed).

---

## `notes push`

```text
agent-trace notes push [--remote NAME]
```

| Option | Default | Purpose |
|--------|---------|---------|
| **`--remote`** | `origin` | Git remote name for ref transport. |

Uses git CLI under the hood to push **`refs/notes/agent-trace`**.

---

## `notes pull`

```text
agent-trace notes pull [--remote NAME]
```

| Option | Default | Purpose |
|--------|---------|---------|
| **`--remote`** | `origin` | Git remote to fetch notes from. |

---

## Related

- [Git notes concept](../concepts/git-notes.md)
- [Getting started](../getting-started.md) — refspec setup via `init`
