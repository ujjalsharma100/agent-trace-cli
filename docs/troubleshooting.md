# Troubleshooting

Symptoms-first guidance. When in doubt, run **`agent-trace doctor`** and **`agent-trace status`** from the repository root (or pass **`--project`** where supported).

---

## `blame` shows No attribution for most lines

**Likely causes**

1. **No commit yet** after the agent edited — the ledger is produced at **`commit-link`** (post-commit). Commit your work.
2. **Fresh clone** without local `ledgers.jsonl` — initialize (`init`) and either **pull** synced data or **fetch git notes** depending on your team workflow.
3. **Line outside ledger coverage** — rare edge cases when content cannot be matched; **No attribution** is intentional.

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

- `agent-trace remote show <name>` — confirm URL parses and a `token_ref` (`global:…` or `env:…`) is present.
- `agent-trace doctor` — surfaces the resolved scope (`token org='acme', org-scoped`). A missing `Remote '<name>' token matches URL` line means the binding is unhealthy.
- `401 Unauthorized` — token rejected. Re-bind with `agent-trace remote set-token <name> --token "$NEW_TOKEN"` (or `--token-env`). Tokens persist on the server; the CLI just holds the secret.
- `403 / 404 project_not_found` — the URL's `<project_slug>` was never registered on the server, or your token isn't scoped for it. Run `agent-trace project create <url>` (with an org-scoped token carrying `projects:write`, or `AGENT_TRACE_ADMIN_SECRET`).
- `scope check (org_slug_mismatch)` / `(project_scope_mismatch)` — the token's org/project does not match the URL. Either fix the URL with `remote set-url` or rotate to a token from the right scope.

`AGENT_TRACE_TOKEN` and `set globaluser` do **not** affect push/pull — only the per-remote token binding does. See [push/pull/sync — Authentication](reference/push-pull-sync.md#authentication).

Corporate TLS inspection / custom CA issues are outside agent-trace's stdlib HTTP client assumptions — you may need a proxy or different network path.

---

## Remote URL rejected with "missing the project path"

agent-trace requires the **slug grammar** on every remote URL, in one of two shapes:

```
<scheme>://<host>[:port]/<org_slug>/<project_slug>          # standalone service
<scheme>://<host>[:port]/at/<org_slug>/<project_slug>       # behind an /at/ API gateway
```

Bare-host URLs (`https://traces.acme.com`) are rejected up front. `/at/` is the only accepted extra path segment — any other over-deep path (`https://traces.acme.com/acme/myrepo/extra`) is rejected. Re-add with the full path, then register the project if it doesn't exist yet:

```bash
agent-trace remote add origin https://traces.acme.com/acme/myrepo \
    --token "$AT_TOKEN" --create
```

See [Project identity](concepts/project-identity.md) and [project create](reference/projects-create.md).

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
