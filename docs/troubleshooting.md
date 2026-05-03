# Troubleshooting

Symptoms-first guidance. When in doubt, run **`agent-trace doctor`** and **`agent-trace status`** from the repository root (or pass **`--project`** where supported).

---

## `blame` shows UNKNOWN for most lines

**Likely causes**

1. **No commit yet** after the agent edited — the ledger is produced at **`commit-link`** (post-commit). Commit your work.
2. **Fresh clone** without local `ledgers.jsonl` — initialize (`init`) and either **pull** synced data or **fetch git notes** depending on your team workflow.
3. **Line outside ledger coverage** — rare edge cases when content cannot be matched; UNKNOWN is intentional.

**Mitigations**

- Ensure **`post-commit`** hook calls `agent-trace commit-link`.
- After rebase, confirm **`post-rewrite`** ran or manually run `rewrite-ledger` with the stdin contract (normally automatic).

---

## Hooks “not firing”

**Checks**

```bash
agent-trace hooks status
agent-trace doctor
```

**Common reasons**

- **Global hooks** not installed — run `hooks setup-global`.
- File being edited is **outside** an initialized git repo — recording is skipped silently by design.
- JSON syntax errors in **`hooks.json`** / **`settings.json`** from manual edits — validate JSON.

---

## `context` shows previews but `--full` is empty or errors

**Likely causes**

- Transcript moved or deleted on disk after the trace was recorded (`file://` URL stale).
- Insufficient permissions to read transcript path.

---

## Remote `push` / `pull` fails with auth errors

**Checks**

- Prefer setting **`AGENT_TRACE_TOKEN`** for CI, or `agent-trace set globaluser …` for interactive dev machines.
- `remote show <name>` to confirm URL and that a token ref is present.
- Corporate TLS inspection / custom CA issues are outside agent-trace’s stdlib HTTP client assumptions — you may need a proxy or different network path.

---

## Viewer does not open or port in use

- Confirm **`agent-trace-viewer`** exists on PATH (`which agent-trace-viewer`).
- Re-run **`install.sh`** if npm/build step was skipped earlier.
- Try a different machine / check firewall rules for localhost.

---

## Wrong repo when running from a parent directory

Pass explicit project disambiguation:

```bash
agent-trace blame src/foo.ts --project /abs/path/to/repo
agent-trace context src/foo.ts --project /abs/path/to/repo
agent-trace viewer --project /abs/path/to/repo
```

---

## `config set` rejects my boolean

Use explicit **`true`** / **`false`** or other accepted tokens — see [Configuration — Boolean tokens](configuration.md#cli-agent-trace-config-set-field-value).

---

## Still stuck

Collect:

- `agent-trace --version`
- `agent-trace doctor` output
- `agent-trace status` output (redact URLs/tokens)
- Whether you use **global** or **project** hooks

Then open a GitHub issue with that bundle.
