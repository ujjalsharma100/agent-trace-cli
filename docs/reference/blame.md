# Command: `blame`

```text
agent-trace blame <file> [OPTIONS]
```

**Purpose:** Print **line-level AI attribution** for `file` using **ledger-backed deterministic** logic (plus optional inline structures from git notes when applicable). Attribution is **binary**: each line is either **AI** or **No attribution**. If the introducing commit has **no** usable ledger/note data for a line, it is **No attribution** — the tool does **not** invent attribution and never claims a line was human-written.

---

## Positional arguments

| Arg | Required | Description |
|-----|----------|-------------|
| **`file`** | yes | Path to the file to analyze, relative to cwd or absolute. |

---

## Options (exhaustive)

| Option | Short | Type | Default | Purpose |
|--------|-------|------|---------|---------|
| **`--line`** | **`-l`** | int | none | Restrict output to a **single** 1-based line number. |
| **`--range`** | **`-r`** | string | none | Restrict to inclusive line range **`START-END`** (example: `10-25`). |
| **`--project`** | **`-p`** | string | none | Git repo root **path** or registry **`project_id`** when cwd is ambiguous or wrong repo. |
| **`--json`** | | flag | off | Emit structured JSON instead of human text. |
| **`--show-no-attribution`** | | flag | off | Include lines classified as **No attribution** in text output; default text view **omits** them for readability. |
| **`--require-attribution`** | | flag | off | Exit **non-zero** if any selected line is **No attribution** — intended for **CI gates**. |

**`--line` vs `--range`:** If both are supplied, behavior follows argparse / implementation order — prefer passing **only one** narrowing flag for clarity.

---

## Output semantics (text mode)

Groups contiguous lines with the same attribution into ranges, tagged **`[AI]`** or **`[NO ATTRIBUTION]`**. Attribution is binary — there are no `HUMAN`, `MIXED`, or `UNKNOWN` labels. In `--json`, the corresponding `kind` values are **`AI`** and **`NO_ATTRIBUTION`**.

---

## Output semantics (`--json`)

Machine-readable structure suitable for tooling. Each segment includes metadata sufficient to join back to traces (for example `trace_id`, model/tool fields when present).

---

## Exit codes

| Code | When |
|------|------|
| **`0`** | Success. |
| **`1`** | Generic failure (bad path, not a git file, internal error). |
| **`1`** | **`--require-attribution`** triggered because at least one line lacks proof. |

---

## Examples

```bash
agent-trace blame src/api/handlers.go
agent-trace blame src/api/handlers.go -l 42
agent-trace blame src/api/handlers.go -r 10-200
agent-trace blame src/api/handlers.go --json
agent-trace blame src/api/handlers.go --show-no-attribution
agent-trace blame src/api/handlers.go --require-attribution
agent-trace blame src/api/handlers.go -p /Users/dev/work/backend
```

---

## Related

- [context](context.md) — conversation text behind AI segments.
- [Attribution ledger concept](../concepts/attribution-ledger.md).
