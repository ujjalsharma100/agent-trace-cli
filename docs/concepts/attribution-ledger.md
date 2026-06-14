# Concepts — Attribution ledger

The **ledger** is a per-commit, per-repository artifact that answers one **binary** question for each line of a file at this commit: *does this line match AI-edit evidence, or not?* **`agent-trace blame`** reads the ledger (and optional inline git-note data) **only** — it does not infer missing data.

---

## When the ledger is built

The **`post-commit`** hook runs **`agent-trace commit-link`**, which:

- Links the commit to relevant **trace ids** and session state.
- Computes deterministic **line-level** classifications from **hashes of line contents** recorded in traces, combined with the **edit sequence** in the session.

So until you **commit**, there is **no ledger row** for those working-tree changes, and blame reports **No attribution** for those lines (honest “no proof yet”).

---

## After history rewrite

`git rebase`, `git commit --amend`, and similar operations change commit SHAs. The **`post-rewrite`** hook runs **`agent-trace rewrite-ledger`**, which consumes git’s mapping of old→new SHAs on stdin and **updates `ledgers.jsonl`** accordingly.

If you bypass hooks or operate in a clone without ledger data, blame cannot fabricate attribution.

---

## Labels you will see

Attribution is **binary**. Every line is one of two kinds:

| Kind | Text tag | JSON `kind` | Meaning |
|------|----------|-------------|---------|
| **AI** | `[AI]` | `AI` | The line matches AI-authored trace evidence under the deterministic ledger rules. |
| **No attribution** | `[NO ATTRIBUTION]` | `NO_ATTRIBUTION` | Everything else — no matching AI evidence for that line. An **honest absence of proof**, not a guess. |

agent-trace never emits **HUMAN**, **MIXED**, or **UNKNOWN**: it only ever asserts *“this line matches an AI trace,”* or it makes no claim at all (**No attribution**). It does **not** assert that a human wrote a line.

---

## Cross-file and refactor cases

The ledger builder can correlate hashes **across files** when appropriate (for example moves/splits that preserve identifiable line content). Regardless of how the content moved, the guarantee is the same: **output is ledger-driven**, not heuristic scoring.

---

## CI usage

`agent-trace blame` supports **`--require-attribution`**: exit non-zero if any blamed line has **No attribution** — useful as a **guardrail** when you require complete AI provenance for certain paths.

See [blame reference](../reference/blame.md).
