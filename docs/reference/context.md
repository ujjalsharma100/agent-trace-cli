# Command: `context`

```text
agent-trace context <file> [OPTIONS]
```

**Purpose:** After resolving **AI-attributed** (and related) line ranges via the same underlying attribution machinery as `blame`, fetch **conversation transcript** metadata: sizes, previews, optional full text, keyed by `conversation_url` (typically `file://…` transcript paths).

---

## Positional arguments

| Arg | Required | Description |
|-----|----------|-------------|
| **`file`** | yes | File whose AI-associated conversations you want to inspect. |

---

## Options (exhaustive)

| Option | Short | Type | Default | Purpose |
|--------|-------|------|---------|---------|
| **`--lines`** | **`-l`** | string | none | Line range **`START-END`** to focus attribution (example: `10-50`). |
| **`--project`** | **`-p`** | string | none | Git repo root path or `project_id` for disambiguation. |
| **`--full`** | | flag | off | Include **entire** transcript text in output (can be **very large**). |
| **`--json`** | | flag | off | Structured JSON for automation / subagents. |
| **`--query`** | **`-q`** | string | none | Echoed through in output for **subagent instruction** patterns (host-defined). |

---

## Modes

| Mode | Flags | Typical use |
|------|-------|-------------|
| **Summary** | default | Quick preview + stats without flooding context windows. |
| **Full transcript** | `--full` | Deep inspection; prefer delegating to a subagent when stats show huge transcripts (see `rule add context-for-agents`). |

---

## JSON fields (representative)

When **`--json`**, expect per-segment objects including (non-exhaustive): `start_line`, `end_line`, `attribution` (`ai` / `no_attribution`), `model_id`, `tool`, `trace_id`, `confidence`, `conversation_id`, `conversation_size`, `preview`, optional `summary` (session summary text when configured), optional echoed `query` when **`--query`** is set. With **`--full`**, also `conversation_content`.

Exact keys should be treated as **versioned** — snapshot a sample from your installed build when writing strict parsers.

---

## Exit codes

**`0`** on success; **`1`** on resolution errors (missing project init, bad paths, etc.).

---

## Examples

```bash
agent-trace context src/ml/train.py
agent-trace context src/ml/train.py -l 10-120
agent-trace context src/ml/train.py -l 10-120 --full
agent-trace context src/ml/train.py --json
agent-trace context src/ml/train.py -l 5-40 -q "Why was dropout removed?"
```

---

## Related

- [blame](blame.md)
- [rule](rule.md) (`context-for-agents`)
