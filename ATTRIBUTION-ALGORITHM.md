# Attribution Algorithm

This document describes the **AI attribution algorithm** used by agent-trace to associate lines of code with AI-generated content. The same logical algorithm is implemented in two places:

- **Service** (`agent-trace-service/attribution.py`) — used when the CLI runs in remote mode and POSTs blame data to `/api/v1/blame`.
- **CLI (local)** (`agent-trace-cli/agent_trace/blame.py`) — used when the CLI runs in local mode against `.agent-trace/traces.jsonl` and `.agent-trace/commit-links.jsonl`.

The design is **signal-based** and **tiered**: we gather evidence (signals), score candidate traces, map scores to confidence tiers (1–6), and only attribute when we have sufficient structural evidence.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Inputs: Git Blame Data](#2-inputs-git-blame-data)
3. [Candidate Finding](#3-candidate-finding)
4. [File Filtering](#4-file-filtering)
5. [Scoring and Signals](#5-scoring-and-signals)
6. [Evidence Gating](#6-evidence-gating)
7. [Tier and Confidence](#7-tier-and-confidence)
8. [Content Hash](#8-content-hash)
9. [Line Ranges](#9-line-ranges)
10. [Service vs CLI Implementation Notes](#10-service-vs-cli-implementation-notes)
11. [End-to-End Flow](#11-end-to-end-flow)

---

## 1. Overview

**Goal:** For a given line (or segment of lines) in a file, determine whether that code was introduced by an AI-assisted edit and, if so, which trace (AI conversation/session) it came from, with a confidence tier.

**Approach:**

1. Run **git blame** (porcelain format) to get, per line or segment: **commit SHA**, **parent SHA**, **author timestamp**, and **content**.
2. Compute a **content hash** of the blamed content (normalized, SHA-256 prefix).
3. **Find candidate traces** using up to three strategies: commit link, revision match, time window.
4. **Filter** candidates to those that actually touch the blamed file (not just any file in the same commit).
5. **Score** each candidate using weighted signals (commit link, content hash, revision, range, timestamp).
6. **Gate** attribution: require at least one *structural* signal and one of: range evidence, or commit_link + content_hash, or commit_link + revision_parent.
7. Map the **best score** and **signals** to a **tier** (1–6) and a **confidence** value.
8. Return the best matching trace (or no attribution).

**Design principles:**

- **Structural over temporal:** Timestamp alone is never enough; we require commit link, content hash, revision, or range evidence to avoid false positives from manual edits in the same time window.
- **File scoping:** A commit link can associate a commit with traces that touched *any* changed file. We only attribute lines in file F to traces that actually have F in their `files` array.
- **Same weights, same tiers:** Service and CLI use identical signal weights and tier thresholds so behavior is consistent across local and remote modes.

---

## 2. Inputs: Git Blame Data

For each **blame segment** (consecutive lines attributed to the same commit), we have:

| Input           | Source                    | Description |
|----------------|---------------------------|-------------|
| `commit_sha`   | `git blame`               | The commit that introduced these lines (blame commit). |
| `parent_sha`   | `git rev-parse <commit>^` | Parent of the blame commit. |
| `timestamp`    | `git log -1 --format=%aI`  | Author date of the blame commit (ISO-8601). |
| `content_lines`| `git blame --porcelain`   | The actual line(s) content. |
| `file_path`    | Resolved path             | Path of the file, relative to repo root (same as stored in traces). |

From `content_lines` we compute a **content hash** (see [§8 Content Hash](#8-content-hash)).

**Segment grouping (CLI):** The CLI parses `git blame --porcelain`, groups consecutive lines that share the same commit SHA into segments, and computes one content hash per segment. The **representative line number** for attribution is the segment’s midpoint (e.g. `(start_line + end_line) // 2`).

---

## 3. Candidate Finding

We collect candidate traces using **three strategies**, then merge and deduplicate by `trace_id`. All strategies are attempted; order below is the priority in which we add candidates.

### 3.1 Strategy A: Commit Link

- **Service:** `db.get_commit_link(project_id, blame_commit)` → if present, `db.find_traces_by_ids(project_id, link["trace_ids"])`.
- **CLI (local):** Look up `commit_sha` in `commit-links.jsonl`; for each linked `trace_id`, find the trace in the in-memory list from `traces.jsonl`.

Commit links are created by the post-commit hook and record which traces were “active” when the commit was made. These are the strongest candidates but can include traces that touched *other* files in the same commit, so we must filter by file later.

### 3.2 Strategy B: Parent Revision Match

- **Service:** `db.find_traces_by_revision(project_id, blame_parent)`.
- **CLI (local):** Iterate traces where `vcs.revision == parent_sha` and (after merge) filter by file.

Traces recorded at the **parent** revision are plausible: the edit that produced the blame commit likely happened right after that revision.

### 3.3 Strategy C: Time Window (fallback)

- Used only when we have **fewer than 5 candidates** so far.
- **Service:** `db.find_traces_in_time_window(project_id, since, until)` with `since = blame_timestamp - 24h`, `until = blame_timestamp + 1h`.
- **CLI (local):** Filter traces by `timestamp` in the same window and by file.

This is a weak fallback; timestamp alone is not enough for attribution (see [§6 Evidence Gating](#6-evidence-gating)).

### 3.4 Deduplication and File Filter

- Candidates from all strategies are merged; duplicates (same `trace_id`) are dropped.
- **Critical:** We then keep only traces that **touch the blamed file** (see [§4 File Filtering](#4-file-filtering)).

---

## 4. File Filtering

A trace’s `files` array lists the files it touched. We require that the blamed **file path** matches at least one entry in that array.

**Path matching (both implementations):**

- Exact: `trace_path == file_path`.
- Lenient: `trace_path.endswith(file_path)` or `file_path.endswith(trace_path)` so that e.g. `vite.config.js` and `frontend/vite.config.js` can match.

This avoids attributing lines in `src/foo.js` to a trace that only touched `.gitignore` in the same commit.

---

## 5. Scoring and Signals

Each candidate trace is scored by summing **weights** for signals that fire. The same weights are used in service and CLI.

| Signal              | Weight | Description |
|---------------------|--------|-------------|
| `commit_link`       | 40     | Trace ID is in the commit link’s `trace_ids` for the blame commit. |
| `content_hash`       | 30     | Content hash of the blamed segment matches a content hash stored in the trace for this file (at a range covering the line). |
| `revision_parent`   | 15     | Trace’s `vcs.revision` equals (or is a prefix of) the blame **parent** commit SHA. |
| `revision_ancestor` | 8      | *(Documented; not currently implemented in scoring.)* Trace’s revision is an ancestor of the blame commit. |
| `range_match`       | 10     | The representative line number falls **inside** a recorded range (file/conversation/change) for this file. |
| `range_overlap`     | 5      | The line is within 5 lines of a recorded range boundary (near but not inside). |
| `timestamp_match`   | 5      | Trace timestamp is considered plausible relative to the commit (service: simple validity check; CLI: any trace with timestamp in time window already passed candidate filter). |

**Score** = sum of weights for all signals that fired. **Signals** = list of signal names (e.g. `["commit_link", "content_hash", "range_match"]`).

**Content hash match:** In the service, we take the hash from the file entry that covers the blamed line (conversation → change → file-level). In the CLI, we collect all hashes from the matched file entry and check if the segment’s content hash matches any of them (prefix match; see [§8 Content Hash](#8-content-hash)).

**Revision match:** Both implementations support abbreviated SHAs: if `trace_revision` and `blame_parent` match on the minimum length (at least 7 characters), we count `revision_parent`.

**Note:** `revision_ancestor` is listed in tier logic and in comments as a structural signal with weight 8, but **no implementation currently sets this signal** (doing so would require e.g. `git merge-base --is-ancestor`). So the maximum score from revision-related signals today is from `revision_parent` only.

---

## 6. Evidence Gating

We do **not** attribute solely based on a high numeric score. We require:

1. **At least one structural signal**  
   I.e. one of: `commit_link`, `content_hash`, `revision_parent`, `revision_ancestor`, `range_match`, `range_overlap`.  
   `timestamp_match` alone is **not** sufficient (avoids attributing every line edited in the same 24h window as any AI trace).

2. **At least one of the following evidence combinations:**
   - **Range evidence:** `range_match` or `range_overlap`, or  
   - **Strong evidence:** `commit_link` **and** `content_hash`, or  
   - **Commit + revision:** `commit_link` **and** `revision_parent`.

If these are not satisfied, we return **no attribution** (tier `None`, confidence 0) even if the best candidate has a positive score.

---

## 7. Tier and Confidence

Among candidates that pass evidence gating, we take the **best-scoring** trace and map its **(score, signals)** to a **tier** and then to a **confidence** value.

### 7.1 Tier Rules

- **Tier 1:** score ≥ 95 **and** `commit_link` **and** `content_hash` in signals.  
  “Provably certain” — commit link plus content hash match.
- **Tier 2:** score ≥ 80 (and gating already satisfied).  
  “Effectively certain.”
- **Tier 3:** score ≥ 60.  
  “Very high confidence.”
- **Tier 4:** score ≥ 45.  
  “High confidence.”
- **Tier 5:** score ≥ 25.  
  “Medium confidence.”
- **Tier 6:** score &gt; 0 but below 25.  
  “Suggestive.”

If score ≤ 0 or no structural signal is present, tier is **None** (no attribution).

### 7.2 Confidence (numeric)

| Tier | Confidence |
|------|------------|
| None | 0.0  |
| 1    | 1.0  |
| 2    | 0.999|
| 3    | 0.95 |
| 4    | 0.85 |
| 5    | 0.70 |
| 6    | 0.40 |

These values are used for display and API output; the tier is the primary semantic level.

---

## 8. Content Hash

**Purpose:** Tie blamed content to a trace without relying only on commit or range.

**Computation (same in CLI and in trace ingestion):**

1. Concatenate the segment’s lines with `\n`.
2. Normalize line endings: `\r\n` and `\r` → `\n`.
3. SHA-256 the UTF-8 bytes, take the first 16 hex characters, and store with a `sha256:` prefix (e.g. `sha256:a1b2c3d4e5f67890`).

**Matching:**

- Strip optional `sha256:` prefix and compare case-insensitively.
- Compare on the **minimum** of the two lengths (so 8-char and 16-char hashes can match on the shorter prefix).

Hashes can appear in the trace at:

- File entry level: `file_entry["content_hash"]`
- Conversation level: `conv["content_hash"]` (optionally with range)
- Change level: `change["content_hash"]` (optionally with range)

Service: we take the hash from the file entry (conversation → change → file) that **covers** the blamed line. CLI: we collect all hashes from the matched file entry and check the segment hash against any of them.

---

## 9. Line Ranges

Traces store **ranges** as `start_line` and `end_line` (1-based, inclusive) at:

- File entry: `file_entry["start_line"]`, `file_entry["end_line"]`
- Conversations: `conv["start_line"]`, `conv["end_line"]` (and in CLI, also `conv["ranges"][]`)
- Changes: `change["start_line"]`, `change["end_line"]`

**Range check:**

- **Exact:** `start_line ≤ line_number ≤ end_line` → `range_match` (+10).
- **Overlap:** Line within **5 lines** of any range boundary → `range_overlap` (+5).  
  Margin constant: `OVERLAP_MARGIN = 5`.

For “best range” in the result, we pick the range that contains the line (prefer smallest containing span) or, if none contains it, the range whose boundary is closest to the line.

---

## 10. Service vs CLI Implementation Notes

| Aspect | Service | CLI (local) |
|--------|---------|-------------|
| **Data source** | PostgreSQL (`traces`, `commit_links`) | `.agent-trace/traces.jsonl`, `.agent-trace/commit-links.jsonl` |
| **Trace ID field** | `trace_id` | `id` (in JSONL trace object) |
| **Trace timestamp** | `trace_timestamp` (DB column) | `timestamp` in trace JSON |
| **Candidate by revision** | `find_traces_by_revision(project_id, blame_parent)` | In-memory filter `vcs.revision == parent_sha` |
| **Candidate by time** | `find_traces_in_time_window` (DB) | Filter by parsed `timestamp` in ±24h window |
| **Content hash in trace** | From entry that *covers* the line (`_extract_content_hash`) | All hashes from file entry (`_extract_content_hashes`), then any match |
| **Timestamp signal** | `_timestamp_plausible` (valid datetime; no real commit date in scoring context) | Any candidate that passed time-window filter gets timestamp signal |
| **Result shape** | `AttributionResult` (dataclass) → dict for API | Dict with `tier`, `confidence`, `trace_id`, `model_id`, `signals`, etc. |
| **Conversation content** | Fetched from DB (`get_conversation_content`) if `conversation_url` present | Read from `file://` URL if local path |

**Shared:**

- Same signal weights and tier thresholds.
- Same evidence gating (structural signal + range or commit_link+content_hash or commit_link+revision_parent).
- Same content hash computation for the blamed segment.
- Same range overlap margin (5 lines).
- Same file-matching rules (exact or suffix match).

---

## 11. End-to-End Flow

### Service (remote)

1. Client runs `git blame --porcelain`, groups into segments, computes content hashes, and POSTs to `/api/v1/blame` with `project_id`, `file_path`, and `blame_data` (per-segment: `start_line`, `end_line`, `commit_sha`, `parent_sha`, `content_hash`, `timestamp`).
2. Service calls `attribute_line()` once per segment (using a representative line, e.g. midpoint).
3. For each segment: get commit link → find candidates (link, revision, time) → filter by file → score → gating → tier → build `AttributionResult`.
4. Segments are merged (adjacent same trace + same tier), then returned as `attributions` in the JSON response.

### CLI (local)

1. CLI runs `git blame --porcelain`, parses and groups into segments, computes content hashes.
2. Loads `traces.jsonl` and `commit-links.jsonl` from `.agent-trace/`.
3. For each segment: resolve parent_sha and commit date (cached) → find candidates (link, revision, time) → filter by file → score with `_score_trace_local` → gating → tier → build attribution dict (with optional conversation summary from `file://`).
4. Adjacent segments with same `trace_id` and `tier` are merged (`_merge_attributions`).
5. Output: terminal formatting or JSON.

---

## References

- **Service attribution:** `agent-trace-service/attribution.py`
- **Service blame API:** `agent-trace-service/agent_trace_service.py` (`blame_file`), `app.py` (`POST /api/v1/blame`)
- **CLI blame (local + remote):** `agent-trace-cli/agent_trace/blame.py`
- **Database queries:** `agent-trace-service/database_service.py` (`find_traces_by_ids`, `find_traces_by_revision`, `find_traces_in_time_window`, `get_commit_link`)
- **Models:** `agent-trace-service/model.py` (`AttributionResult`, `CommitLink`)
