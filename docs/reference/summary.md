# Command: `summary`

```text
agent-trace summary <ACTION> ...
```

**Purpose:** Configure **pluggable session-end summarization**: an arbitrary command (or built-in preset) reads the **raw transcript** on **stdin** and prints a **short summary** on **stdout**. agent-trace stores stdout keyed by `conversation_url`.

`ACTION` is **required**.

---

## `summary enable`

```text
agent-trace summary enable --command CMD [--timeout SECONDS]
```

| Option | Dest / meaning | Required | Default | Purpose |
|--------|----------------|----------|---------|---------|
| **`--command`** | `CMD` string | **yes** | — | Shell-invoked executable: **stdin** = transcript bytes, **stdout** = summary text. |
| **`--timeout`** | seconds | no | **30** in typical defaults when omitted | Subprocess wall-clock limit; must be positive int when provided. |

**Project requirement:** Initialized project.

**Side effects:** Sets `summary.enabled` + `summary.command` in `project-config.json` (and timeout if supplied).

---

## `summary disable`

```text
agent-trace summary disable
```

Turns off summarization (`summary.enabled` false).

---

## `summary presets`

```text
agent-trace summary presets
```

Lists **built-in** preset aliases with descriptions (local CLI wrappers).

---

## `summary use`

```text
agent-trace summary use <preset_alias> [--model MODEL] [--timeout SECONDS]
```

| Positional | Allowed values |
|------------|------------------|
| **`preset_alias`** | `claude-summary`, `cursor-summary`, `ollama-summary` |

| Option | Applies to | Purpose |
|--------|------------|---------|
| **`--model`** | primarily **`ollama-summary`** | Model name passed to `ollama run` (ignored by other presets). |
| **`--timeout`** | all | Same as `enable`. |

**Effect:** Writes a `summary.command` that invokes an internal **`summary preset-run …`** indirection so the stdin/stdout contract stays uniform.

**Preset behaviors (high level):**

| Alias | External tool | Transport notes |
|-------|---------------|-----------------|
| **`claude-summary`** | `claude -p "<prompt>"` | Prompt may be large; OS argv limits can bite huge transcripts. |
| **`cursor-summary`** | `cursor agent …` | Prompt on **stdin** to avoid argv limits (with `--trust` style flags per implementation). |
| **`ollama-summary`** | `ollama run <model>` | Prompt on stdin; default model **`llama3.1:8b`** when `--model` omitted. |

---

## `summary generate`

```text
agent-trace summary generate [--conversation-url URL] [--session-id ID]
```

**Purpose:** Re-run summarization **manually** for:

- A single **`conversation_url`** (usually `file:///…` transcript), **or**
- Every URL referenced by traces in a **`session_id`** / `conversation_id`.

At least one of **`--conversation-url`** or **`--session-id`** is **required**; if both are omitted the command exits **`1`** with a usage message.

---

## `summary show`

```text
agent-trace summary show [COMMIT]
```

| Positional | Default | Purpose |
|------------|---------|---------|
| **`COMMIT`** | `HEAD` | Show `{conversation_url: summary}` map derived for that commit. |

---

## Internal: `summary preset-run`

This action exists for **preset wiring** and is **hidden** from `--help` (`argparse.SUPPRESS`). Humans should use **`summary use`**. Automation authors should not depend on its stability.

```text
agent-trace summary preset-run <preset_alias> [--model MODEL]
```

---

## Storage & downstream consumption

Summaries live in **`session-summaries.jsonl`**. Downstream: **`blame`** / **`context`** / git **notes** builders may incorporate summaries when configured.

---

## Related

- [Session summaries concept](../concepts/session-summaries.md)
- [summary_presets.py](https://github.com/ujjalsharma100/agent-trace-cli/blob/main/agent_trace/summary_presets.py) source for exact subprocess argv.
