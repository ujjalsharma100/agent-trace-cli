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
| **`url`** | Base HTTP URL of the datastore service. |
| **Token** | Provided via **`--token`**, **`--token-env VAR`**, or later `set-token`; persisted by reference where applicable. |

---

## `remote add`

```text
agent-trace remote add <name> <url> [--token STR] [--token-env VAR]
```

| Positional | Description |
|------------|-------------|
| **`name`** | Remote identifier. |
| **`url`** | Service root URL. |

| Option | Description |
|--------|-------------|
| **`--token`** | Inline token string (discouraged in shared terminals). |
| **`--token-env`** | Read token from named environment variable at add time. |

**Errors:** Duplicate name / invalid URL / missing auth → stderr + exit **1** per implementation.

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

## `remote set-token`

```text
agent-trace remote set-token <name> [--token STR] [--token-env VAR]
```

Refresh credentials. At least one of **`--token`** or **`--token-env`** should be supplied (see `--help` for validation rules on your version).

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
