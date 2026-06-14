# Concepts — Git notes

Git notes attach metadata to **commit objects** without rewriting commits. agent-trace uses the ref **`refs/notes/agent-trace`** with a **versioned JSON** payload so teams can **`git fetch`** / **`git push`** attribution-related metadata alongside the repository.

---

## Why notes exist

Local JSONL under `AGENT_TRACE_HOME` is powerful on your machine but **does not travel** with `git clone` the way blobs and trees do. Git notes provide a **portable channel** for:

- **Trace ids** and summary **stats** tied to a commit.
- Optional **inline ledger** sections (configurable; increases note size).
- Optional **summary** maps (`conversation_url` → summary text).
- Optional **prompts** and **all_session_conversations** sections (both included by default on new `agent-trace init` unless you turn them off in project config).

The exact JSON shape is governed by schemas in the repository (see `schemas/git-note.schema.json`).

---

## Refspec configuration

During **`agent-trace init`**, when a remote named **`origin`** exists, the tool attempts to add the appropriate **fetch/push refspecs** so notes flow with ordinary git operations. If you rename remotes or use nonstandard layouts, you may need to adjust git config manually.

---

## CLI surface

The **`agent-trace notes`** command family covers:

- **`show`** — print the JSON for a commit.
- **`attach`** — build a note from local ledger/config and attach to a commit.
- **`rebuild`** / **`backfill`** — regenerate notes for ranges or time windows.
- **`strip`** — remove optional sections to shrink notes.
- **`push`** / **`pull`** — transport the notes ref to/from a named git remote (defaults to **`origin`**).

See the exhaustive [notes reference](../reference/notes.md).

---

## Interaction with blame

When configured and present, git-note inline structures can participate in attribution resolution alongside local ledgers. If neither source covers a line, blame returns **No attribution**.
