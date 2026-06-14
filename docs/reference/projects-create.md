# Reference — `agent-trace project create`

Register a project on a remote `agent-trace-service` so that a remote URL with the matching slug can receive sync traffic.

## Synopsis

```
agent-trace project create <url> [--token TOKEN | --token-env VAR]
                                  [--name NAME] [--description TEXT]
```

## Arguments

| Flag | Description |
|------|-------------|
| `<url>` (positional) | Full remote URL: `<scheme>://<host>/<org_slug>/<project_slug>`. |
| `--token TOKEN` | Org-scoped bearer token carrying the `projects:write` scope. |
| `--token-env VAR` | Environment variable that holds the token (never persisted). |
| `--name NAME` | Optional human-readable display name for the project. |
| `--description TEXT` | Optional description. |

If `--token`/`--token-env` are not provided, the CLI looks for `AGENT_TRACE_ADMIN_SECRET` in the environment and uses the admin path (`X-Admin-Secret` on the service) instead.

## URL grammar

The URL must include both org and project slugs, in one of two accepted shapes:

```
https://traces.acme.com/acme/myrepo          # standalone service
https://traces.acme.com/at/acme/myrepo       # behind an /at/ API gateway
```

Slugs match `^[a-z0-9][a-z0-9._-]{0,63}$` — lowercase, start with `[a-z0-9]`, max 64 chars. Bare-host URLs (`https://traces.acme.com`) are rejected with a clear error; `/at/` is the only accepted extra path segment, so other over-deep paths (`https://traces.acme.com/acme/myrepo/extra`) are still rejected.

## Auth

The service accepts two paths for `POST /api/v1/projects`:

1. **Org-scoped token** with `projects:write` in its scopes. Project-scoped tokens are intentionally rejected — they cannot create new projects.
2. **`X-Admin-Secret`** matching the service's `ADMIN_SECRET`. The CLI sends this when `AGENT_TRACE_ADMIN_SECRET` is set; the body's `org_id` defaults to the service's default org (`00000000-0000-0000-0000-000000000001`) unless you override it.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Project created (HTTP 201). The CLI prints the JSON record to stdout. |
| `2` | Project already exists (HTTP 409). |
| `1` | Any other error (auth failure, bad URL, network error, slug shape rejected). |

## Examples

Register and bind in one step:

```
agent-trace remote add origin https://traces.acme.com/acme/myrepo \
    --token "$AT_TOKEN" --create
```

Register from a different machine (no remote configured locally yet):

```
agent-trace project create https://traces.acme.com/acme/myrepo \
    --token-env AT_TOKEN
```

Admin path (self-hosted, default org):

```
AGENT_TRACE_ADMIN_SECRET=dev-admin-secret \
    agent-trace project create http://localhost:5000/default/myrepo
```

## Related

- [Project identity](../concepts/project-identity.md)
- [Remotes & sync](../concepts/remotes-and-sync.md)
- [push/pull/sync](push-pull-sync.md)
