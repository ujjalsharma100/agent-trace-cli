# Global flags

These apply to the **top-level** `agent-trace` program **before** the subcommand name.

---

## `--version`

Prints the embedded version string (for example `agent-trace 0.1.0`) and **exits without** running a subcommand.

```bash
agent-trace --version
```

**Stdio:** stdout only. **Exit code:** `0`.

---

## No subcommand

```bash
agent-trace
```

Prints the **root help** listing all subcommands and exits **`0`** (this is an argparse design choice — not an error).

---

## Subcommand help

Each subcommand supports `-h` / `--help` after the subcommand name:

```bash
agent-trace blame --help
agent-trace notes attach --help
```

---

## What does *not* exist globally today

The CLI does **not** currently expose global flags for:

- Debug / trace logging level
- Config file path override (use `AGENT_TRACE_HOME` instead)
- Non-interactive mode for **all** commands (`config reset` has partial `--yes` support only)

If you need those behaviors for packaging, open a feature request on the repository tracker.
