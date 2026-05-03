# Concepts — Attribution ledger

The **ledger** is a per-commit, per-repository artifact that answers: *for each line of a file at this commit, was the content last attributable to AI edits, human edits, or both?* **`agent-trace blame`** reads the ledger (and optional inline git-note data) **only** — it does not infer missing data.

---

## When the ledger is built

The **`post-commit`** hook runs **`agent-trace commit-link`**, which:

- Links the commit to relevant **trace ids** and session state.
- Computes deterministic **line-level** classifications from **hashes of line contents** recorded in traces, combined with the **edit sequence** in the session.

So until you **commit**, there is **no ledger row** for those working-tree changes, and blame will show **UNKNOWN** for those lines (honest “no proof yet”).

---

## After history rewrite

`git rebase`, `git commit --amend`, and similar operations change commit SHAs. The **`post-rewrite`** hook runs **`agent-trace rewrite-ledger`**, which consumes git’s mapping of old→new SHAs on stdin and **updates `ledgers.jsonl`** accordingly.

If you bypass hooks or operate in a clone without ledger data, blame cannot fabricate attribution.

---

## Labels you will see

In CLI text and JSON output, expect categories along the lines of:

| Kind | Meaning (simplified) |
|------|----------------------|
| **AI** | Line range matches AI-authored trace evidence under ledger rules. |
| **HUMAN** | Human-authored under the same deterministic rules. |
| **MIXED** | Evidence combines human and AI influence for that range. |
| **UNKNOWN** | No ledger coverage (or unusable note) for that line — **not a guess**. |

JSON schemas and stats in git notes may use related terminology such as **NO_ATTRIBUTION** in schema descriptions; the CLI’s blame help text refers to **UNKNOWN** for the same honest-absence case.

---

## Cross-file and refactor cases

The ledger builder can correlate hashes **across files** when appropriate (for example moves/splits that preserve identifiable line content). Edge cases are documented in the repository’s internal engineering notes; the user-facing guarantee remains: **output is ledger-driven**, not heuristic scoring.

---

## CI usage

`agent-trace blame` supports **`--require-attribution`**: exit non-zero if any blamed line would be UNKNOWN — useful as a **guardrail** when you require complete provenance for certain paths.

See [blame reference](../reference/blame.md).
