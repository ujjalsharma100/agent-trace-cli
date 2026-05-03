# Concepts — Session summaries

Some teams want a **short prose summary** of each assistant conversation stored next to traces, without shipping entire transcripts in every git note. agent-trace supports a **pluggable summarizer**: a shell command that reads the **raw transcript on stdin** and writes the **summary on stdout**.

---

## Storage model

Summaries are stored in **`session-summaries.jsonl`** under the project directory. Rows are keyed by **`conversation_url`** (typically a `file://` URL pointing at the agent’s transcript on disk). **Latest row wins** for a given URL.

When a summary exists, **`blame`** and **`context`** can show summary text **instead of** only a raw preview / URL where applicable.

---

## Built-in presets

Instead of writing your own script, you can enable packaged presets that wrap local CLIs (`claude`, `cursor agent`, `ollama`). Each preset ultimately still conforms to the stdin/stdout contract by delegating to a subprocess.

Details, aliases, and timeouts: [summary reference](../reference/summary.md).

---

## Timeouts and PATH

Summarization runs with a configurable timeout (default **30 seconds** unless overridden). GUI-launched agents often have a minimal **`PATH`**; the preset layer augments common binary locations (for example Homebrew prefixes) so `ollama` / `cursor` binaries are discoverable when installed for an interactive shell.

---

## Security & cost note

Summaries send transcript text to whichever command you configure — which may be a **cloud model** or a **local** one. Treat **`summary.command`** like any other secret-bearing automation: review before enabling in regulated environments.
