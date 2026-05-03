# Command: `viewer`

```text
agent-trace viewer [--project PATH]
```

**Purpose:** Start the **local web file viewer**: browse the repository tree, open files, and inspect **git blame** alongside **agent-trace blame** in the UI.

---

## Options

| Option | Short | Type | Default | Purpose |
|--------|-------|------|---------|---------|
| **`--project`** | **`-p`** | path | cwd | Repository root to open. Use when launching from outside the repo. |

---

## Runtime details

- The viewer is installed by **`install.sh`** under `AGENT_TRACE_HOME/viewer/` with a launcher **`agent-trace-viewer`** on PATH.
- Default URL is typically **`http://127.0.0.1:8765`** (confirm in your version’s launcher output if the port changes).
- Requires a working **local backend + static frontend**; if files are missing, re-run the installer.

---

## Exit codes

**`0`** when launch succeeds; non-zero if the launcher cannot start subprocesses or locate assets.

---

## Examples

```bash
agent-trace viewer
agent-trace viewer --project /Users/me/repos/monolith
agent-trace viewer -p ..
```

---

## Related

- [Installation](../installation.md) — how viewer artifacts are placed on disk.
