# Concepts — Project identity & storage

agent-trace has **two** notions of identity, and you only need to think about both at the same time when you bind a remote.

| Layer | Where it lives | What it identifies |
|-------|----------------|---------------------|
| **Local id** | `<git-common-dir>/agent-trace-id` (an opaque uuid like `at-` + 32 hex) | The on-disk data directory under `<AGENT_TRACE_HOME>/projects/<id>/`. Used by every local command (`record`, `blame`, `context`, `summary`). |
| **Remote slug** | The remote URL: `<scheme>://<host>/<org_slug>/<project_slug>` | The server-side bucket. Used as the wire `project_id` on every push/pull call. Mirrors GitHub's `org/repo` model. |

The two layers are deliberately decoupled. **Standalone** users (no service) only ever see the local id — they install the CLI, run `init`, and `blame`/`context` work without anyone registering anything. **Team** users put the remote URL on their checkouts; the slug in the URL becomes the wire identity, and Bob's `pull` sees Alice's pushed work because they're using the same `<org>/<project>` slug.

---

## AGENT_TRACE_HOME

| Path | Role |
|------|------|
| `<home>/config.json` | **Global** settings: tokens (per-remote), opt-in flags. |
| `<home>/projects.json` | **Registry** metadata (optional enrichment; see `projects` / `adopt`). |
| `<home>/projects/<local_id>/` | **All per-project** JSONL, `project-config.json`, `remotes.json`, sync cursors. |

Override the root with `AGENT_TRACE_HOME` (heavily used in tests; also useful if you want a separate disk). The path layout uses the **local id** — even after you bind a remote with a slug, the on-disk dir keeps its uuid name (renaming it would break running tools and any other working tree pointed at the same anchor).

---

## Local id (the anchor)

Resolution order inside a git working tree (`storage.resolve_project_id`):

1. **Anchor file** — `<git-common-dir>/agent-trace-id`, a single line containing `at-<32 hex>`.
   - **Created** on `init` (or whenever `resolve_project_id(..., create=True)` runs without an existing anchor).
   - **Shared** across linked **git worktrees** because they share `git-common-dir`.
2. **Path-derived fallback** — If there is no anchor yet (un-init'd repo), the tool falls back to a sanitized form of the realpath of the repo root (legacy `-Users-you-src-foo`). This lets `status`/`blame` resolve to *some* directory before `init`.
3. **Non-git paths** — Outside a git repo, a path-derived id is still produced for detached-edit and test scenarios.

A fresh clone gets a **new** local anchor. That is fine — the local anchor never travels with the repo, and it is **not** what teammates share. Team sharing happens at the remote-slug layer, not the anchor layer.

---

## Remote slug (the canonical id)

When you run

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo
```

the CLI parses the URL into `(base_url, org_slug, project_slug) = (https://traces.acme.com, acme, myrepo)` and stores all four fields in `remotes.json`. (Deployments served behind a shared API gateway use the `…/at/<org>/<project>` form, e.g. `https://traces.acme.com/at/acme/myrepo`; the org and project slugs are the same, and sync routes are issued under that `/at/…` prefix.) From that point on:

- **push** and **pull** send `project_id=myrepo` on the wire.
- The server stores rows under `(org_id=resolve('acme'), project_id='myrepo')`. This is the existing `(org_id, project_id)` UNIQUE composite key — slugs are unique within an org, just like GitHub repos within an org.
- Two checkouts that bind the same URL push/pull from the same bucket. That is the whole point.

Slug shape (CHECK-enforced server-side, mirrored in the client):

```
^[a-z0-9][a-z0-9._-]{0,63}$
```

Lowercase only. Must start with `[a-z0-9]`. Up to 64 chars. `Acme`, `-bad`, and over-deep paths (beyond the optional `/at/` gateway prefix) — all rejected with a clear error.

### Registering a project

Projects are explicit on the server. Before pushing, register the slug:

```
agent-trace project create https://traces.acme.com/acme/myrepo --token <ORG_TOKEN>
```

Or in one step alongside `remote add`:

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo \
    --token "$AT_TOKEN" --create
```

See [reference/projects-create](../reference/projects-create.md). The `--create` path requires either a token with the `projects:write` scope or `AGENT_TRACE_ADMIN_SECRET` set in the environment (admin secret matches the service's `ADMIN_SECRET`).

### Why explicit registration

The service rejects pushes to unregistered slugs with `404 project_not_found`. This mirrors GitHub: you create the repo, then push. It also stops anyone with an org-scoped token from squatting random slugs by sending traffic at them — registration carries an explicit `projects:write` gate that project-scoped tokens cannot pass.

---

## adopt vs init

| Command | Purpose |
|---------|---------|
| `agent-trace adopt [path]` | Registers metadata and prints the local `project_id`. Useful for scripting and registry visibility. |
| `agent-trace init` | Full project setup: config, hooks as applicable, git hooks, notes refspec wiring, anchor creation. |
| `agent-trace project create <url>` | Creates the **server-side** project under the URL's slug. Independent of the local anchor; talk to the service from any repo. |

See the [projects & adopt](../reference/projects-adopt.md) reference.

---

## Multi-repo working directories

Some editor layouts open a parent folder containing multiple git roots. Commands that need a repo context accept `--project` (or equivalent) with either:

- A path to the **git repository root**, or
- A `project_id` string registered in the workspace.

When in doubt, pass `--project /absolute/path/to/repo`.

---

## Related configuration

- `remote.default` — which named remote `push`/`pull`/`sync` prefer when you omit `--remote`.
- Per-project config (`project-config.json`) and per-project `remotes.json` are stored alongside data files. See [Configuration](../configuration.md).
