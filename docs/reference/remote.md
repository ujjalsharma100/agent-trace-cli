# Command: `remote`

```text
agent-trace remote <ACTION> ...
```

**Purpose:** Manage **named HTTP remotes** (analogous to `git remote`) for the **current initialized project**. Remotes store **URL** + **token reference** metadata in project configuration; secrets are never printed in full.

**Prerequisite:** Run from an **initialized** project (`agent-trace init`); otherwise commands exit with guidance.

---

## Common concepts

| Term | Meaning |
|------|---------|
| **`name`** | Short handle (`origin`, `team`, …). |
| **`url`** | Full service URL including the project path: `<scheme>://<host>[:port]/<org_slug>/<project_slug>` (or `…/at/<org_slug>/<project_slug>` when the service sits behind an `/at/` API gateway). Bare-host URLs are rejected. |
| **Token** | Provided via **`--token`** (stored in `~/.agent-trace/config.json` under `tokens.<project_id>::<name>`, referenced as `global:<key>`) or **`--token-env VAR`** (referenced as `env:VAR`, never persisted). |

The URL grammar is enforced both client- and server-side. The path segments become the wire `org_slug` and `project_slug`; the slug must exist on the server before push/pull will accept traffic for it. See [Project identity](../concepts/project-identity.md).

---

## `remote add` {#remote-add}

```text
agent-trace remote add <name> <url> [--token STR | --token-env VAR] [--create]
```

| Positional | Description |
|------------|-------------|
| **`name`** | Remote identifier. |
| **`url`** | Full URL with `<org_slug>/<project_slug>` path. |

| Option | Description |
|--------|-------------|
| **`--token`** | Inline token string (discouraged in shared terminals). Persisted in global config under a per-project key. |
| **`--token-env`** | Read token from the named environment variable at every push/pull (never persisted). |
| **`--create`** | Also register the project on the server (`POST /api/v1/projects`) before storing the remote. Requires `--token` / `--token-env` with `projects:write` scope, or `AGENT_TRACE_ADMIN_SECRET`. A `409 already exists` from the server is treated as a soft success — the remote is still bound. |

**Pre-flight scope check:** When a token is provided, `remote add` calls `GET /api/v1/auth/whoami` on the URL's host and refuses to bind a remote whose URL points at one org while the token belongs to another. This catches the most common slug-mistype before any data moves.

**Errors:** Duplicate name / invalid URL / missing auth / scope mismatch → stderr + exit **1**.

---

## `remote list`

```text
agent-trace remote list
```

Prints **`name`**, **`url`**, and token reference summary `(set)` / `(no auth)` for each entry.

---

## `remote show`

```text
agent-trace remote show <name>
```

Pretty multi-line details with **masked** secrets.

**Exit:** **`1`** if unknown name.

---

## `remote set-url`

```text
agent-trace remote set-url <name> <url>
```

Updates only the URL for an existing remote.

---

## `remote set-token` {#remote-set-token}

```text
agent-trace remote set-token <name> [--token STR | --token-env VAR]
```

Refresh credentials. **Exactly one** of `--token` / `--token-env` must be supplied; passing neither exits non-zero with `Provide --token or --token-env.`

---

## `remote remove`

```text
agent-trace remote remove <name>
```

Deletes the named remote entry.

---

## `remote rename`

```text
agent-trace remote rename <old_name> <new_name>
```

Renames a remote; fails if **`new_name`** already exists.

---

## `remote default`

```text
agent-trace remote default <name>
```

Sets **`remote.default`** in project config so **`push` / `pull` / `sync`** pick this remote when **`--remote`** is omitted.

---

## Related

- [push / pull / sync](push-pull-sync.md)
- [Remotes & sync concept](../concepts/remotes-and-sync.md)
