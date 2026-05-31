"""
agent-trace CLI — terminal commands for AI code tracing.

Zero external dependencies — uses only the Python standard library.

Commands:
    agent-trace init              Initialize tracing for the current project
    agent-trace status            Show tracing status
    agent-trace reset             Reconfigure tracing settings
    agent-trace config show       Show persisted configuration
    agent-trace config set        Set one configuration field
    agent-trace config reset      Reset one configuration field/group
    agent-trace hooks setup-global  Install global hooks for all tools
    agent-trace hooks remove-global Remove global hooks
    agent-trace hooks status      Show global hook status
    agent-trace record            Record a trace from stdin (used by hooks)
    agent-trace commit-link       Link current commit to traces (called by git hook)
    agent-trace blame <file>      Show AI attribution for a file
    agent-trace context <file>    Get conversation context for AI-attributed code
    agent-trace rule add <name>   Add a prebuilt rule for a coding agent
    agent-trace rule remove <name> Remove a rule
    agent-trace rule show         Show which rules are configured
    agent-trace rule list         List available prebuilt rules
    agent-trace viewer            Open the file viewer (browse files, git + agent-trace blame)
    agent-trace remote add/list/show/set-url/set-token/remove/rename/default
    agent-trace push              Push local traces to remote
    agent-trace pull              Pull remote traces to local
    agent-trace sync              Push + pull in one go
    agent-trace set globaluser    Set a global auth token
    agent-trace remove globaluser Remove the global auth token
    agent-trace projects          List registered project IDs (or: projects show <id>)
    agent-trace adopt [path]      Register a git repo and print its stable project_id
    agent-trace notes ...         Git notes (attach, rebuild, show, push, pull, …)
    agent-trace summary ...       Pluggable session summaries (enable, generate, show)
    agent-trace doctor            Check hooks, config, remotes, and tooling

Global flags:
    --telemetry on|off|status     Opt-in anonymous usage telemetry (default off)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .config import (
    get_global_config,
    get_global_config_file,
    get_project_config,
    save_global_config,
    save_project_config,
)
from .blame import blame_file
from .commit_link import create_commit_link
from .context import context_command
from .registry import list_projects, lookup_or_create_project_id
from .trace import (
    cli_resolve_project_root,
    discover_ambiguous_repo_roots,
    get_vcs_info,
    git_repo_root_for_path,
)
from .hooks import (
    AGENT_TRACE_CMD,
    adapter_names,
    configure_project_hooks,
    iter_adapters,
    remove_global_hooks,
    setup_global_hooks,
)
from .hooks.git import (
    configure_git_hooks,
    configure_git_notes_refspecs,
    configure_git_post_rewrite_hook,
)
from .rules import add_rule, remove_rule, show_rules, list_available_rules, TOOL_CHOICES
from .record import record_from_stdin
from .rewrite import rewrite_ledgers
from .remote import (
    ProjectRegistrationError,
    RemoteUrlError,
    TokenScopeError,
    WhoamiUnsupportedError,
    add_remote as remote_add,
    assert_token_matches_url,
    get_default_remote,
    list_remotes as remote_list,
    parse_remote_url,
    register_project_via_remote,
    remove_remote as remote_remove,
    rename_remote as remote_rename,
    set_default_remote,
    set_remote_token,
    set_remote_url,
    show_remote as remote_show,
    whoami as remote_whoami,
)
from .sync import pull as sync_pull, push as sync_push, status as sync_status
from .telemetry import (
    maybe_report_cli_run,
    set_telemetry_enabled,
    telemetry_status_lines,
)
from .summary_presets import (
    DEFAULT_OLLAMA_MODEL,
    PRESET_ALIASES,
    build_preset_command,
    list_summary_presets,
    run_summary_preset,
)

VERSION = "0.1.0"

VIEWER_BIN = os.path.expanduser("~/.agent-trace/bin/agent-trace-viewer")


def _project_hook_paths_for(adapter) -> list[str]:
    """Return project-scoped config paths declared by the adapter.

    Adapters expose ``project_config_paths()`` to list every relative
    path that signals "agent-trace hooks installed at project scope".
    The CLI just iterates — no per-tool branches.
    """
    paths_fn = getattr(adapter, "project_config_paths", None)
    if callable(paths_fn):
        try:
            return list(paths_fn())
        except Exception:
            return []
    return []


# -------------------------------------------------------------------
# Interactive helpers (replaces click.prompt / click.confirm)
# -------------------------------------------------------------------

def _prompt(message, default=None, choices=None):
    """Interactive text prompt."""
    hint = ""
    if choices:
        hint += f" ({'/'.join(choices)})"
    if default is not None:
        hint += f" [{default}]"

    while True:
        try:
            value = input(f"{message}{hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

        if not value:
            if default is not None:
                return default
            continue

        if choices and value.lower() not in [c.lower() for c in choices]:
            print(f"  Please choose from: {', '.join(choices)}")
            continue

        return value


def _confirm(message, default=True):
    """Interactive yes / no prompt."""
    hint = " [Y/n]" if default else " [y/N]"
    try:
        value = input(f"{message}{hint}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)

    if not value:
        return default
    return value in ("y", "yes")


# ===================================================================
# init
# ===================================================================

def cmd_init(_args):
    """Zero-prompt init — applies sensible local defaults and wires up hooks.

    Like ``git init``: creates local-only state. Remote sharing is opt-in and
    set up later via ``agent-trace remote add`` (and ``agent-trace notes push``
    / ``agent-trace push``).
    """
    config = get_project_config()
    if config is not None:
        print("agent-trace is already initialized for this project.")
        print("Use 'agent-trace reset' to change configuration.")
        return

    cwd = os.getcwd()
    if not git_repo_root_for_path(cwd):
        print("agent-trace init: not a git repository.", file=sys.stderr)
        print("Create or enter a git repo, then run init again.", file=sys.stderr)
        sys.exit(1)

    if get_vcs_info(cwd) is None:
        print("agent-trace init: repository has no commits yet.", file=sys.stderr)
        print("Create at least one commit, then run 'agent-trace init' again.", file=sys.stderr)
        sys.exit(1)

    project_config: dict = {
        "notes": {
            "enabled": True,
            "include_ledger": False,
            "include_summary": True,
            "include_prompts": True,
            "all_session_conversations": False,
        },
    }
    save_project_config(project_config)

    from .storage import get_project_config_path, resolve_project_id

    pid = resolve_project_id(cwd, create=True)

    print("Initializing agent-trace...\n")
    print(f"  Project id:   {pid}")
    print(f"  Data dir:     {get_project_config_path(pid).parent if pid else '?'}")

    # Git notes — defaults on; per-line ledger off unless configured
    print("  Git notes:    enabled (summary + prompts; per-line ledger off by default)")

    notes_remote = "origin"
    notes_refspec_ok = False
    if os.path.isdir(".git"):
        notes_refspec_ok = configure_git_notes_refspecs(remote_name=notes_remote)
    if notes_refspec_ok:
        print(f"  Note refspec: configured on remote \"{notes_remote}\"")
    else:
        print(f"  Note refspec: skipped (no git remote \"{notes_remote}\" yet — "
              "rerun 'agent-trace reset' after adding one)")

    # Hooks — install everything by default, skip if global already handles it.
    # Driven by the adapter registry: new agents are picked up automatically.
    for adapter in iter_adapters():
        label = adapter.display_name or adapter.name
        global_path = adapter.global_config_path()
        if adapter.is_installed():
            print(f"  {label} hooks: global ({global_path}) — already set")
        else:
            adapter.inject(AGENT_TRACE_CMD, global_install=False)
            print(f"  {label} hooks: configured (project-level)")

    if os.path.isdir(".git"):
        configure_git_hooks()
        print("  Git hooks:    post-commit + post-rewrite installed")

    print()
    print("agent-trace initialized. Everything runs locally.")
    print()
    print("  Change config:    agent-trace reset")
    print("  Add a sync remote: agent-trace remote add origin <url>")
    print("  Push to remote:    agent-trace push")
    print("  Ship notes with commits: notes travel with 'git push' once a remote is set.")


def _probe_remote_health(base_url: str) -> tuple[bool, str]:
    """Return (ok, detail) for an agent-trace HTTP service (tries /health, /api/health)."""
    base = base_url.rstrip("/")
    last_err = "unreachable"
    for path in ("/health", "/api/health"):
        try:
            with urllib.request.urlopen(base + path, timeout=8) as resp:
                if 200 <= getattr(resp, "status", 200) < 400:
                    return True, f"{path} -> OK"
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            last_err = str(e) or repr(e)
    return False, last_err


@dataclasses.dataclass
class DoctorFixHints:
    """Auto-fix opportunities detected during doctor (never includes unreachable remotes)."""

    chmod_home: bool = False
    chmod_global_config: Path | None = None
    init_needed: bool = False
    global_hooks_needed: bool = False
    git_hooks_needed: bool = False
    git_rewrite_only: bool = False
    notes_refspec_needed: bool = False


def _doctor_git_has_origin(cwd: str) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _doctor_diagnose(cwd: str) -> tuple[list[str], list[str], list[str], DoctorFixHints]:
    """Collect doctor messages and fix hints for ``cwd``."""
    from .storage import (
        get_agent_trace_home,
        get_project_config_path,
        resolve_project_id,
    )

    ok: list[str] = []
    warn: list[str] = []
    err: list[str] = []
    hints = DoctorFixHints()

    def _git_out(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True,
                text=True,
                timeout=12,
            )
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    home = get_agent_trace_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".doctor_write_probe"
        probe.write_text("")
        try:
            probe.unlink()
        except OSError:
            pass
        ok.append(f"Global storage is writable ({home})")
    except OSError as e:
        err.append(f"Global storage not writable ({home}): {e}")
        if home.exists():
            hints.chmod_home = True

    gf = get_global_config_file()
    if gf.is_file():
        mode = gf.stat().st_mode & 0o777
        if mode & 0o077:
            warn.append(f"Global config should be mode 600 (currently {oct(mode)})")
            hints.chmod_global_config = gf
        else:
            ok.append("Global config permissions look good (600)")

    cfg = get_project_config()
    pid = resolve_project_id(cwd, create=False)
    initialised = cfg is not None

    if initialised:
        if pid:
            ok.append(f"Project config readable ({get_project_config_path(pid)})")
        else:
            ok.append("Project config readable")
    else:
        warn.append("Project not initialised (run agent-trace init)")
        hints.init_needed = True

    for adapter in iter_adapters():
        label = adapter.display_name or adapter.name
        global_path = adapter.global_config_path()
        if adapter.is_installed():
            ok.append(f"{label} global hooks configured ({global_path})")
            continue
        project_paths = _project_hook_paths_for(adapter)
        any_present = False
        for project_path in project_paths:
            if os.path.exists(project_path):
                any_present = True
                try:
                    raw = open(project_path, encoding="utf-8").read()
                except OSError:
                    warn.append(f"Could not read {project_path}")
                    continue
                if AGENT_TRACE_CMD in raw:
                    ok.append(f"{label} project hooks mention {AGENT_TRACE_CMD} ({project_path})")
                else:
                    warn.append(
                        f"{label} {project_path} present but no {AGENT_TRACE_CMD} hook found",
                    )
                break
        if not any_present and initialised:
            warn.append(f"{label} hooks not configured (no global or project hooks)")
            hints.global_hooks_needed = True

    git_hook_ok = False
    git_rewrite_ok = False
    try:
        if os.path.exists(".git/hooks/post-commit"):
            with open(".git/hooks/post-commit", encoding="utf-8", errors="replace") as f:
                git_hook_ok = "agent-trace commit-link" in f.read()
    except OSError:
        pass
    try:
        if os.path.exists(".git/hooks/post-rewrite"):
            with open(".git/hooks/post-rewrite", encoding="utf-8", errors="replace") as f:
                git_rewrite_ok = "agent-trace rewrite-ledger" in f.read()
    except OSError:
        pass
    if git_hook_ok:
        ok.append("Git post-commit hook calls agent-trace commit-link")
    elif initialised and os.path.isdir(".git"):
        warn.append("Git post-commit hook missing or does not call agent-trace commit-link")
        hints.git_hooks_needed = True
    if git_rewrite_ok:
        ok.append("Git post-rewrite hook calls agent-trace rewrite-ledger")
    elif initialised and os.path.isdir(".git"):
        warn.append("Git post-rewrite hook missing or does not call agent-trace rewrite-ledger")
        if git_hook_ok:
            hints.git_rewrite_only = True

    if os.path.isdir(".git"):
        fetch_all = _git_out(["config", "--get-all", "remote.origin.fetch"])
        push_all = _git_out(["config", "--get-all", "remote.origin.push"])
        blob = f"{fetch_all}\n{push_all}"
        if "refs/notes/agent-trace" in blob:
            ok.append("Git notes refspec present for origin (refs/notes/agent-trace)")
        else:
            warn.append(
                "Git notes refspec not configured for origin "
                "(optional; run init or git config --add remote.origin.fetch +refs/notes/agent-trace:...)",
            )
            if _doctor_git_has_origin(cwd):
                hints.notes_refspec_needed = True

    if pid:
        from .remote import (
            RemoteUrlError,
            TokenScopeError,
            WhoamiUnsupportedError,
            assert_token_matches_url,
            get_remote as _doctor_get_remote,
            get_remote_base_url,
            get_remote_health_probe_base_url,
            get_remote_org_slug,
            get_remote_project_slug,
            get_remote_token,
            parse_remote_url,
        )

        remotes = remote_list(pid)
        if not remotes:
            ok.append("No sync remotes configured (optional: agent-trace remote add)")
        for r in remotes:
            url = r.get("url") or ""
            name = r.get("name", "?")
            if not url:
                warn.append(f"Remote '{name}' has no URL")
                continue
            try:
                parse_remote_url(url)
            except RemoteUrlError as e:
                err.append(
                    f"Remote '{name}' URL is malformed: {e}. "
                    f"Run `agent-trace remote set-url {name} <scheme>://<host>/at/<org>/<project>` "
                    f"or ``<scheme>://<host>/<org>/<project>``."
                )
                continue
            conf = _doctor_get_remote(pid, name) or {}
            health_base = get_remote_health_probe_base_url(conf)
            sync_base = get_remote_base_url(conf)
            org = get_remote_org_slug(conf) or "?"
            proj = get_remote_project_slug(conf) or "?"
            alive, detail = _probe_remote_health(health_base)
            if alive:
                ok.append(f"Remote '{name}' responds at {health_base} (org={org}, project={proj}) ({detail})")
            else:
                warn.append(f"Remote '{name}' not reachable at {health_base} ({detail})")
                continue

            # Token / org / project scope check. Skip silently if no token
            # or if the URL didn't carry the expected slugs (the malformed
            # URL branch above already reported it). On a working scope we
            # add a positive line so the user can see the binding is sound.
            token = get_remote_token(conf)
            if not token:
                warn.append(f"Remote '{name}' has no auth token configured (scope check skipped)")
                continue
            url_org = get_remote_org_slug(conf)
            url_project = get_remote_project_slug(conf)
            if not url_org or not url_project:
                continue
            try:
                info = assert_token_matches_url(
                    sync_base, token,
                    expected_org_slug=url_org,
                    expected_project_slug=url_project,
                    allow_unsupported=True,
                )
            except TokenScopeError as e:
                err.append(
                    f"Remote '{name}' token/scope mismatch ({e.code}): {e} "
                    f"Run `agent-trace remote set-token {name} ...` with a token "
                    f"for org '{url_org}', or `agent-trace remote set-url {name} ...` "
                    f"to point at the org the token belongs to."
                )
                continue
            if info.get("_unsupported"):
                warn.append(
                    f"Remote '{name}' service does not implement /auth/whoami "
                    "(upgrade the service to enable strict scope checks)"
                )
            else:
                token_org = info.get("org_slug") or "?"
                token_proj_scope = info.get("project_id_scope")
                scope_desc = (
                    f"project-scoped to '{token_proj_scope}'"
                    if token_proj_scope else "org-scoped"
                )
                ok.append(
                    f"Remote '{name}' token matches URL "
                    f"(token org='{token_org}', {scope_desc})"
                )

    summ = (cfg or {}).get("summary") if cfg else None
    if isinstance(summ, dict) and summ.get("enabled"):
        cmd = summ.get("command") or ""
        parts = shlex.split(cmd) if cmd.strip() else []
        if not parts:
            err.append("summary.enabled but summary.command is empty")
        else:
            exe = parts[0]
            if os.path.isfile(exe) or shutil.which(exe):
                ok.append("Summary command first token is executable or on PATH")
            else:
                warn.append(f"Summary command may not run (not found: {exe})")

    return ok, warn, err, hints


def cmd_doctor(args):
    """Report hook installation, config validity, remotes, and optional tools."""
    cwd = os.getcwd()
    fix = getattr(args, "fix", False)
    dry_run = getattr(args, "dry_run", False)
    yes = getattr(args, "yes", False)

    def _print_report(ok: list[str], warn: list[str], err: list[str]) -> None:
        print("agent-trace doctor\n")
        for line in ok:
            print(f"  OK  {line}")
        for line in warn:
            print(f"  !!  {line}")
        for line in err:
            print(f"  XX  {line}")
        print()

    def _apply_fixes(hints: DoctorFixHints) -> list[str]:
        """Return human-readable lines describing actions taken (no-op if dry_run)."""
        from .storage import get_agent_trace_home

        lines: list[str] = []
        home = get_agent_trace_home()

        if hints.chmod_home and home.exists():
            msg = f"chmod u+rwx {home}"
            if dry_run:
                lines.append(f"[dry-run] would: {msg}")
            else:
                try:
                    os.chmod(home, stat.S_IRWXU)
                    lines.append(f"Applied: {msg}")
                except OSError as e:
                    lines.append(f"Skipped chmod on {home}: {e}")

        if hints.chmod_global_config is not None:
            p = hints.chmod_global_config
            msg = f"chmod 600 {p}"
            if dry_run:
                lines.append(f"[dry-run] would: {msg}")
            else:
                try:
                    os.chmod(p, 0o600)
                    lines.append(f"Applied: {msg}")
                except OSError as e:
                    lines.append(f"Skipped chmod on {p}: {e}")

        if hints.init_needed:
            if dry_run:
                lines.append("[dry-run] would: agent-trace init")
            elif yes:
                cmd_init(args)
                lines.append("Applied: agent-trace init (--yes)")
            elif sys.stdin.isatty():
                if _confirm("Initialize agent-trace for this project?", default=True):
                    cmd_init(args)
                    lines.append("Applied: agent-trace init (confirmed)")
                else:
                    lines.append("Skipped: agent-trace init (not confirmed)")
            else:
                lines.append("Skipped: agent-trace init (non-interactive; use --yes to apply)")

        if hints.global_hooks_needed:
            msg = "hooks setup-global (all registered adapters)"
            if dry_run:
                lines.append(f"[dry-run] would: agent-trace {msg}")
            else:
                setup_global_hooks()
                lines.append(f"Applied: agent-trace {msg}")

        if hints.git_hooks_needed:
            msg = "install git post-commit + post-rewrite hooks"
            if dry_run:
                lines.append(f"[dry-run] would: {msg}")
            else:
                configure_git_hooks(cwd)
                lines.append(f"Applied: {msg}")
        elif hints.git_rewrite_only:
            msg = "install git post-rewrite hook"
            if dry_run:
                lines.append(f"[dry-run] would: {msg}")
            else:
                configure_git_post_rewrite_hook(cwd)
                lines.append(f"Applied: {msg}")

        if hints.notes_refspec_needed:
            msg = "configure git notes refspec for remote.origin"
            if dry_run:
                lines.append(f"[dry-run] would: {msg}")
            else:
                if configure_git_notes_refspecs(project_dir=cwd, remote_name="origin"):
                    lines.append(f"Applied: {msg}")
                else:
                    lines.append(f"Skipped: {msg} (git refused or no origin)")

        return lines

    ok, warn, err, hints = _doctor_diagnose(cwd)

    if fix:
        applied_any = any(
            (
                hints.chmod_home,
                hints.chmod_global_config is not None,
                hints.init_needed,
                hints.global_hooks_needed,
                hints.git_hooks_needed,
                hints.git_rewrite_only,
                hints.notes_refspec_needed,
            ),
        )
        if applied_any:
            print("agent-trace doctor --fix\n")
            action_lines = _apply_fixes(hints)
            for line in sorted(action_lines):
                print(f"  {line}")
            print()
            if not dry_run:
                ok, warn, err, _hints = _doctor_diagnose(cwd)

    _print_report(ok, warn, err)
    if err:
        sys.exit(1)


# ===================================================================
# status
# ===================================================================

def cmd_status(_args):
    config = get_project_config()
    if config is None:
        print("agent-trace is not set up for this project.")
        print("Run 'agent-trace init' to get started.")
        return

    from .storage import (
        get_commit_links_path,
        get_ledgers_path,
        get_project_dir,
        get_traces_path,
        resolve_project_id,
    )
    pid = resolve_project_id(os.getcwd(), create=False)

    print("agent-trace status\n")
    if pid:
        print(f"  Project:    {pid}")
        print(f"  Data dir:   {get_project_dir(pid)}")

        def _count(path):
            if not path.exists():
                return 0
            with open(path) as f:
                return sum(1 for _ in f)

        print(f"  Traces:       {_count(get_traces_path(pid))} recorded")
        print(f"  Commit links: {_count(get_commit_links_path(pid))} recorded")
        print(f"  Ledgers:      {_count(get_ledgers_path(pid))} recorded")

        # Show remote sync info
        try:
            report = sync_status(pid)
            if report.remote_name:
                print(f"\n  Remote '{report.remote_name}' ({report.remote_url}):")
                print(f"    Unpushed: {report.unpushed_traces} traces "
                      f"({report.unattributed_traces} unattributed held back)")
                print(f"              {report.unpushed_ledgers} ledgers")
                print(f"              {report.unpushed_commit_links} commit-links")
                if report.traces_cursor:
                    print(f"    Traces cursor:        {report.traces_cursor}")
                if report.ledgers_cursor:
                    print(f"    Ledgers cursor:       {report.ledgers_cursor}")
                if report.commit_links_cursor:
                    print(f"    Commit-links cursor:  {report.commit_links_cursor}")
                if report.conversations_cursor:
                    print(f"    Conversations cursor: {report.conversations_cursor}")
                print()
                print("  Run 'agent-trace push' to share attributed work.")
                print("  Run 'agent-trace pull' to fetch teammates' changes.")
        except Exception:
            pass

    git_hook_ok = False
    git_rewrite_ok = False
    try:
        if os.path.exists(".git/hooks/post-commit"):
            with open(".git/hooks/post-commit") as f:
                git_hook_ok = "agent-trace commit-link" in f.read()
    except OSError:
        pass
    try:
        if os.path.exists(".git/hooks/post-rewrite"):
            with open(".git/hooks/post-rewrite") as f:
                git_rewrite_ok = "agent-trace rewrite-ledger" in f.read()
    except OSError:
        pass

    def _hook_label(has_global, has_project):
        if has_global:
            return "global"
        if has_project:
            return "project"
        return "not configured"

    print()
    for adapter in iter_adapters():
        label = adapter.display_name or adapter.name
        is_global = adapter.is_installed()
        is_project = any(
            os.path.exists(p) for p in _project_hook_paths_for(adapter)
        )
        print(f"  {label} hook:".ljust(22) + _hook_label(is_global, is_project))
    print(f"  Git post-commit:   {'configured' if git_hook_ok else 'not configured'}")
    print(f"  Git post-rewrite:  {'configured' if git_rewrite_ok else 'not configured'}")


# ===================================================================
# reset
# ===================================================================

def cmd_reset(_args):
    """Reconfigure notes sections, hooks, and git-notes refspecs."""
    config = get_project_config()
    if config is None:
        print("agent-trace is not set up for this project.")
        print("Run 'agent-trace init' to get started.")
        return

    print("Resetting agent-trace configuration...\n")

    notes_cfg = config.get("notes") or {}
    notes_enabled = _confirm(
        "Enable git notes (JSON on refs/notes/agent-trace)?",
        default=bool(notes_cfg.get("enabled", True)),
    )
    new_notes = {"enabled": notes_enabled}
    if notes_enabled:
        new_notes["include_ledger"] = _confirm(
            "Include per-line ledger in notes?",
            default=bool(notes_cfg.get("include_ledger", False)),
        )
        new_notes["include_summary"] = _confirm(
            "Include summaries in notes?",
            default=bool(notes_cfg.get("include_summary", True)),
        )
        new_notes["include_prompts"] = _confirm(
            "Include prompt previews in notes?",
            default=bool(notes_cfg.get("include_prompts", True)),
        )
        new_notes["all_session_conversations"] = _confirm(
            "Include all session conversations in notes (staging window, not only attributed lines)?",
            default=bool(notes_cfg.get("all_session_conversations", False)),
        )

    new_config = dict(config)
    new_config.pop("label", None)
    new_config["notes"] = new_notes
    save_project_config(new_config)
    print("\nConfiguration updated.\n")

    if os.path.isdir(".git"):
        rn = _prompt("Git remote for note refspecs (blank to skip)", default="origin").strip()
        if rn:
            if configure_git_notes_refspecs(remote_name=rn):
                print(f"  -> Git notes refspecs configured for remote \"{rn}\"")
            else:
                print(f"  -> Skipped (no remote \"{rn}\")")

    for adapter in iter_adapters():
        label = adapter.display_name or adapter.name
        if _confirm(f"Reconfigure {label} hook?", default=False):
            adapter.inject(AGENT_TRACE_CMD, global_install=False)
            print(f"  -> {label} hooks configured.")
    if os.path.isdir(".git") and _confirm("Reinstall git hooks?", default=False):
        configure_git_hooks()
        print("  -> Git hooks reinstalled.")


# ===================================================================
# config
# ===================================================================

_DEFAULT_NOTES_CONFIG = {
    "enabled": True,
    "include_ledger": False,
    "include_summary": True,
    "include_prompts": True,
    "all_session_conversations": False,
}

_CONFIG_FIELDS = {
    "notes.enabled",
    "notes.include-ledger",
    "notes.include-summary",
    "notes.include-prompts",
    "notes.all-session-conversations",
    "summary.enabled",
    "summary.command",
    "summary.timeout-seconds",
    "remote.default",
    "global.auth-token",
    "global.capture-detached-edits",
}

_CONFIG_RESET_TARGETS = _CONFIG_FIELDS | {"notes", "summary"}


def _parse_config_bool(value: str) -> bool:
    v = str(value).strip().lower()
    if v in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if v in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    raise ValueError("expected a boolean value (true/false)")


def _redact_config_value(value):
    if isinstance(value, dict):
        redacted = {}
        for key, val in value.items():
            lk = str(key).lower()
            if lk == "install_id" and val:
                redacted[key] = "(anonymous id)"
            elif lk in {"auth_token", "token"} or "secret" in lk:
                redacted[key] = "(set)" if val else val
            elif lk == "tokens" and isinstance(val, dict):
                redacted[key] = {name: "(set)" for name in val}
            else:
                redacted[key] = _redact_config_value(val)
        return redacted
    if isinstance(value, list):
        return [_redact_config_value(item) for item in value]
    return value


def _project_config_or_exit() -> tuple[str, dict]:
    from .storage import resolve_project_id

    cfg = get_project_config()
    if cfg is None:
        print("agent-trace is not set up for this project.", file=sys.stderr)
        print("Run 'agent-trace init' to get started.", file=sys.stderr)
        sys.exit(1)
    pid = resolve_project_id(os.getcwd(), create=False)
    if not pid:
        print("agent-trace config: no project id", file=sys.stderr)
        sys.exit(1)
    return pid, cfg


def _load_safe_remotes(project_id: str) -> dict:
    try:
        from .remote import _load_remotes
    except ImportError:
        return {}
    return _redact_config_value(_load_remotes(project_id))


def _hook_config_status() -> dict:
    git_post_commit = False
    git_post_rewrite = False
    try:
        if os.path.exists(".git/hooks/post-commit"):
            with open(".git/hooks/post-commit") as f:
                git_post_commit = "agent-trace commit-link" in f.read()
    except OSError:
        pass
    try:
        if os.path.exists(".git/hooks/post-rewrite"):
            with open(".git/hooks/post-rewrite") as f:
                git_post_rewrite = "agent-trace rewrite-ledger" in f.read()
    except OSError:
        pass
    out: dict = {}
    for adapter in iter_adapters():
        out[adapter.name] = {
            "global": adapter.is_installed(),
            "project": any(
                os.path.exists(p) for p in _project_hook_paths_for(adapter)
            ),
        }
    out["git"] = {
        "post_commit": git_post_commit,
        "post_rewrite": git_post_rewrite,
    }
    return out


def _full_config_snapshot() -> dict:
    from .storage import get_project_dir, resolve_project_id

    cfg = get_project_config()
    pid = resolve_project_id(os.getcwd(), create=False)
    snapshot = {
        "global": {
            "config": _redact_config_value(get_global_config()),
        },
        "project": None,
        "hooks": _hook_config_status(),
    }
    if pid and cfg is not None:
        snapshot["project"] = {
            "id": pid,
            "data_dir": str(get_project_dir(pid)),
            "config": cfg,
            "remotes": _load_safe_remotes(pid),
        }
    return snapshot


def _print_config_snapshot(snapshot: dict) -> None:
    print("agent-trace config\n")
    project = snapshot.get("project")
    if project:
        print(f"  Project:  {project['id']}")
        print(f"  Data dir: {project['data_dir']}")
        print("\n  Project config:")
        print(_indent_json(project.get("config") or {}))
        print("\n  Remotes:")
        print(_indent_json(project.get("remotes") or {}))
    else:
        print("  Project:  not initialized")

    print("\n  Global config:")
    print(_indent_json(snapshot.get("global", {}).get("config") or {}))
    print("\n  Hooks:")
    print(_indent_json(snapshot.get("hooks") or {}))


def _indent_json(value: dict) -> str:
    text = json.dumps(value, indent=2, sort_keys=True)
    return "\n".join(f"    {line}" for line in text.splitlines())


def _cleanup_empty_mapping(config: dict, key: str) -> None:
    if isinstance(config.get(key), dict) and not config[key]:
        del config[key]


def _bool_to_text(value: bool) -> str:
    return "true" if value else "false"


def _prompt_reset_bool(field: str, default_value: bool) -> bool:
    raw = _prompt(
        f"Reset {field} (press Enter for default)",
        default=_bool_to_text(default_value),
    )
    return _parse_config_bool(raw)


def _interactive_reset_field(field: str) -> None:
    """Interactive reset flow: Enter accepts reset defaults."""
    bool_defaults = {
        "notes.enabled": True,
        "notes.include-ledger": False,
        "notes.include-summary": True,
        "notes.include-prompts": True,
        "notes.all-session-conversations": False,
        "summary.enabled": False,
        "global.capture-detached-edits": False,
    }
    clear_targets = {
        "summary",
        "summary.command",
        "summary.timeout-seconds",
        "remote.default",
        "global.auth-token",
    }

    if field == "notes":
        _pid, cfg = _project_config_or_exit()
        cfg["notes"] = {
            "enabled": _prompt_reset_bool("notes.enabled", True),
            "include_ledger": _prompt_reset_bool("notes.include-ledger", False),
            "include_summary": _prompt_reset_bool("notes.include-summary", True),
            "include_prompts": _prompt_reset_bool("notes.include-prompts", True),
            "all_session_conversations": _prompt_reset_bool("notes.all-session-conversations", False),
        }
        save_project_config(cfg)
        return

    if field in bool_defaults:
        chosen = _prompt_reset_bool(field, bool_defaults[field])
        _set_config_field(field, _bool_to_text(chosen))
        return

    if field in clear_targets:
        if not _confirm(f"Reset {field} to default (clear current value)?", default=True):
            print("No changes made.")
            return
        _reset_config_field(field)
        return

    # Fallback: if new reset targets are added in future.
    _reset_config_field(field)


def _set_config_field(field: str, value: str) -> None:
    if field not in _CONFIG_FIELDS:
        raise ValueError(f"unknown field '{field}'")

    if field.startswith("global."):
        cfg = get_global_config()
        if field == "global.auth-token":
            cfg["auth_token"] = value
        elif field == "global.capture-detached-edits":
            cfg["capture_detached_edits"] = _parse_config_bool(value)
        save_global_config(cfg)
        return

    pid, cfg = _project_config_or_exit()
    if field.startswith("notes."):
        notes = cfg.setdefault("notes", {})
        key = field.split(".", 1)[1].replace("-", "_")
        notes[key] = _parse_config_bool(value)
    elif field == "summary.enabled":
        sm = cfg.setdefault("summary", {})
        sm["enabled"] = _parse_config_bool(value)
    elif field == "summary.command":
        if not value.strip():
            raise ValueError("summary.command cannot be blank")
        sm = cfg.setdefault("summary", {})
        sm["enabled"] = True
        sm["command"] = value
    elif field == "summary.timeout-seconds":
        timeout = int(value)
        if timeout <= 0:
            raise ValueError("summary.timeout-seconds must be positive")
        cfg.setdefault("summary", {})["timeout_seconds"] = timeout
    elif field == "remote.default":
        from .remote import set_default_remote

        set_default_remote(pid, value)
        return
    save_project_config(cfg)


def _reset_config_field(field: str) -> None:
    if field not in _CONFIG_RESET_TARGETS:
        raise ValueError(f"unknown reset target '{field}'")

    if field.startswith("global."):
        cfg = get_global_config()
        if field == "global.auth-token":
            cfg.pop("auth_token", None)
        elif field == "global.capture-detached-edits":
            cfg.pop("capture_detached_edits", None)
        save_global_config(cfg)
        return

    _pid, cfg = _project_config_or_exit()
    if field == "notes":
        cfg["notes"] = dict(_DEFAULT_NOTES_CONFIG)
    elif field.startswith("notes."):
        notes = cfg.setdefault("notes", {})
        key = field.split(".", 1)[1].replace("-", "_")
        notes[key] = _DEFAULT_NOTES_CONFIG[key]
    elif field == "summary":
        cfg.pop("summary", None)
    elif field == "summary.enabled":
        cfg.setdefault("summary", {})["enabled"] = False
    elif field == "summary.command":
        if isinstance(cfg.get("summary"), dict):
            cfg["summary"].pop("command", None)
            _cleanup_empty_mapping(cfg, "summary")
    elif field == "summary.timeout-seconds":
        if isinstance(cfg.get("summary"), dict):
            cfg["summary"].pop("timeout_seconds", None)
            _cleanup_empty_mapping(cfg, "summary")
    elif field == "remote.default":
        if isinstance(cfg.get("remote"), dict):
            cfg["remote"].pop("default", None)
            _cleanup_empty_mapping(cfg, "remote")
    save_project_config(cfg)


def cmd_config(args):
    """Show and mutate persisted configuration."""
    action = getattr(args, "config_action", None)
    if action == "show":
        snapshot = _full_config_snapshot()
        if getattr(args, "json", False):
            print(json.dumps(snapshot, indent=2, sort_keys=True))
        else:
            _print_config_snapshot(snapshot)
        return

    if action == "set":
        try:
            _set_config_field(args.field, args.value)
        except (ValueError, TypeError) as e:
            print(f"agent-trace config set: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Config field '{args.field}' updated.")
        return

    if action == "reset":
        try:
            if getattr(args, "yes", False):
                _reset_config_field(args.field)
            else:
                _interactive_reset_field(args.field)
        except (ValueError, TypeError) as e:
            print(f"agent-trace config reset: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Config field '{args.field}' reset.")
        return

    print("Usage: agent-trace config {show,set,reset}")


# ===================================================================
# hooks (global hook management)
# ===================================================================

# Computed lazily from the adapter registry so newly registered tools
# are picked up automatically by the ``--tool`` flag and by status output.
def _hook_tool_choices() -> list[str]:
    return adapter_names()


def cmd_hooks(args):
    """Manage global hooks for coding tools."""
    action = getattr(args, "hooks_action", None)

    if action == "setup-global":
        tools = getattr(args, "tools", None) or _hook_tool_choices()
        results = setup_global_hooks(tools)
        for tool, ok in results.items():
            if ok:
                print(f"  -> Global {tool} hooks configured")
            else:
                print(f"  !! Failed to configure global {tool} hooks")

    elif action == "remove-global":
        tools = getattr(args, "tools", None) or _hook_tool_choices()
        results = remove_global_hooks(tools)
        for tool, removed in results.items():
            if removed:
                print(f"  -> Global {tool} hooks removed")
            else:
                print(f"  -- Global {tool} hooks were not present")

    elif action == "status":
        print("Global hooks:")
        for adapter in iter_adapters():
            label = adapter.display_name or adapter.name
            state = "configured" if adapter.is_installed() else "not configured"
            print(f"  {label:<12} {state:<16}  ({adapter.global_config_path()})")

    else:
        print("Usage: agent-trace hooks {setup-global,remove-global,status}")
        print("Run 'agent-trace hooks --help' for details.")


# ===================================================================
# record  (called by hooks — reads stdin)
# ===================================================================

def cmd_record(_args):
    try:
        record_from_stdin()
    except Exception:
        # Never crash the coding agent
        pass


# ===================================================================
# commit-link  (called by git post-commit hook)
# ===================================================================

def cmd_commit_link(_args):
    """Create a commit-trace link for the current HEAD commit."""
    try:
        link = create_commit_link()
        if link:
            n = len(link.get("trace_ids", []))
            print(f"agent-trace: linked commit {link['commit_sha'][:8]} to {n} trace(s)")
    except Exception:
        # Never crash — this runs inside a git hook
        pass


# ===================================================================
# rewrite-ledger  (called by git post-rewrite hook)
# ===================================================================

def cmd_rewrite_ledger(_args):
    """Remap ledgers after rebase/amend (called by git post-rewrite hook)."""
    try:
        count = rewrite_ledgers()
        if count:
            print(f"agent-trace: remapped {count} ledger(s)")
    except Exception:
        # Never crash — this runs inside a git hook
        pass


# ===================================================================
# viewer
# ===================================================================

def cmd_viewer(args):
    """Launch the file viewer, or print install instructions if not installed."""
    project_path = getattr(args, "project", None) or os.getcwd()
    if not os.path.isdir(project_path):
        print(f"agent-trace viewer: project path is not a directory: {project_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(VIEWER_BIN) or not os.access(VIEWER_BIN, os.X_OK):
        print("Viewer is not installed.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Re-run install.sh from the agent-trace-cli repo to install the viewer:", file=sys.stderr)
        print("  cd agent-trace-cli && ./install.sh", file=sys.stderr)
        sys.exit(1)

    # Exec the viewer with project path as first argument
    os.execv(VIEWER_BIN, [VIEWER_BIN, project_path])


# ===================================================================
# blame
# ===================================================================

def cmd_blame(args):
    """Show AI attribution for a file."""
    # Parse --range if provided (e.g. "10-25")
    start_line = None
    end_line = None
    if getattr(args, "range", None):
        parts = args.range.split("-", 1)
        try:
            start_line = int(parts[0])
            end_line = int(parts[1]) if len(parts) > 1 else start_line
        except (ValueError, IndexError):
            print(f"Invalid range: {args.range}  (expected format: START-END)")
            sys.exit(1)

    project_dir = None
    if getattr(args, "project", None):
        project_dir = cli_resolve_project_root(args.project)
    else:
        amb = discover_ambiguous_repo_roots()
        if len(amb) > 1:
            print(
                "agent-trace: current directory spans multiple git repositories; "
                "pass --project <path|id> to choose one:",
                file=sys.stderr,
            )
            for r in amb:
                print(f"  {r}", file=sys.stderr)
            sys.exit(1)

    result = blame_file(
        args.file,
        line=getattr(args, "line", None),
        start_line=start_line,
        end_line=end_line,
        show_no_attribution=getattr(args, "show_no_attribution", False),
        require_attribution=getattr(args, "require_attribution", False),
        json_output=getattr(args, "json", False),
        project_dir=project_dir,
    )
    if result is not None:
        print(result)


# ===================================================================
# context
# ===================================================================

def cmd_context(args):
    """Get conversation context for AI-attributed code."""
    project_dir = None
    if getattr(args, "project", None):
        project_dir = cli_resolve_project_root(args.project)
    else:
        amb = discover_ambiguous_repo_roots()
        if len(amb) > 1:
            print(
                "agent-trace: current directory spans multiple git repositories; "
                "pass --project <path|id>:",
                file=sys.stderr,
            )
            for r in amb:
                print(f"  {r}", file=sys.stderr)
            sys.exit(1)

    context_command(
        args.file,
        lines_range=getattr(args, "lines", None),
        full=getattr(args, "full", False),
        json_output=getattr(args, "json", False),
        query=getattr(args, "query", None),
        project_dir=project_dir,
    )


# ===================================================================
# rule
# ===================================================================

def cmd_rule(args):
    """Manage agent rules."""
    rule_action = getattr(args, "rule_action", None)

    rule_tools_help = " | ".join(list(TOOL_CHOICES))
    if rule_action == "add":
        tool = getattr(args, "tool", None)
        if not tool:
            print(f"--tool is required. Use --tool <{rule_tools_help}>", file=sys.stderr)
            sys.exit(1)
        path = add_rule(args.rule_name, tool)
        print(f"Rule '{args.rule_name}' added for {tool}: {path}")

    elif rule_action == "remove":
        tool = getattr(args, "tool", None)
        if not tool:
            print(f"--tool is required. Use --tool <{rule_tools_help}>", file=sys.stderr)
            sys.exit(1)
        path = remove_rule(args.rule_name, tool)
        if path:
            print(f"Rule '{args.rule_name}' removed for {tool}: {path}")
        else:
            print(f"Rule '{args.rule_name}' is not configured for {tool}.")

    elif rule_action == "show":
        active = show_rules()
        if not active:
            print("No agent-trace rules are configured.")
            print("Use 'agent-trace rule list' to see available rules.")
            return
        print("Configured agent-trace rules:\n")
        for entry in active:
            print(f"  {entry['name']:<25} tool: {entry['tool']:<10} {entry['path']}")
        print()

    elif rule_action == "list":
        available = list_available_rules()
        if not available:
            print("No prebuilt rules available.")
            return
        print("Available agent-trace rules:\n")
        for entry in available:
            print(f"  {entry['name']:<25} {entry['description']}")
        print()
        print(f"Add a rule with: agent-trace rule add <name> --tool <{'|'.join(list(TOOL_CHOICES))}>")

    else:
        print("Usage: agent-trace rule {add,remove,show,list}")
        print("Run 'agent-trace rule --help' for details.")


# ===================================================================
# set globaluser
# ===================================================================

def cmd_set_globaluser(args):
    config = get_global_config()
    config["auth_token"] = args.token
    save_global_config(config)
    from .storage import get_global_config_file
    print(f"Global auth token saved to {get_global_config_file()}")


# ===================================================================
# remove globaluser
# ===================================================================

def cmd_remove_globaluser(_args):
    config = get_global_config()
    if "auth_token" in config:
        del config["auth_token"]
        save_global_config(config)
        print("Global auth token removed.")
    else:
        print("No global auth token is currently configured.")


# ===================================================================
# projects / adopt (Phase 1b registry)
# ===================================================================

def cmd_projects(args):
    """List registered projects or show one."""
    import json

    from .registry import get_project_record

    rest = list(getattr(args, "projects_args", None) or [])
    if len(rest) >= 2 and rest[0] == "show":
        rec = get_project_record(rest[1])
        if not rec:
            print(f"agent-trace: unknown project id: {rest[1]}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({"project_id": rest[1], **rec}, indent=2))
        return
    if rest:
        print("Usage: agent-trace projects  |  agent-trace projects show <project_id>", file=sys.stderr)
        sys.exit(1)

    rows = list_projects()
    if not rows:
        print("No registered projects.")
        return
    for row in rows:
        pid = row.get("project_id", "?")
        root = row.get("canonical_root", "")
        print(f"  {pid}  {root}")
    print()


def cmd_adopt(args):
    """Register a repository and print its stable project_id."""
    path = os.path.abspath(os.path.expanduser(getattr(args, "adopt_path", ".") or "."))
    gr = git_repo_root_for_path(path)
    if not gr:
        print("agent-trace adopt: not a git repository", file=sys.stderr)
        sys.exit(1)
    pid = lookup_or_create_project_id(gr)
    print(pid)


# ===================================================================
# project create — register a project on a remote service
# ===================================================================

def cmd_project(args):
    """Server-side project administration (currently: create)."""
    action = getattr(args, "project_action", None)
    if action == "create":
        return _cmd_project_create(args)
    print(
        "Usage: agent-trace project create <remote-url> [--token TOKEN | --token-env VAR] "
        "[--name NAME] [--description TEXT]",
        file=sys.stderr,
    )
    sys.exit(2)


def _cmd_project_create(args):
    url = args.url
    try:
        base_url, org_slug, project_slug = parse_remote_url(url)
    except RemoteUrlError as e:
        print(f"agent-trace project create: {e}", file=sys.stderr)
        sys.exit(1)

    token_value = getattr(args, "token", None)
    token_env = getattr(args, "token_env", None)
    if token_value is None and token_env:
        token_value = os.environ.get(token_env)
    admin_secret = os.environ.get("AGENT_TRACE_ADMIN_SECRET")

    if not token_value and not admin_secret:
        print(
            "agent-trace project create: a token is required. Pass --token / --token-env, "
            "or set AGENT_TRACE_ADMIN_SECRET to use the admin path.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Pre-flight scope check: refuse to create the project if the URL's
    # ``<org_slug>`` doesn't match the token's actual org. Without this,
    # the server would (silently) create the row under the token's org and
    # the local remote would record the wrong org slug — drift the user
    # only notices when traces vanish from "their" org.
    if token_value:
        try:
            assert_token_matches_url(
                base_url, token_value,
                expected_org_slug=org_slug,
                expected_project_slug=project_slug,
            )
        except TokenScopeError as e:
            print(f"agent-trace project create: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        proj = register_project_via_remote(
            base_url, project_slug,
            org_slug=org_slug,
            token=token_value, admin_secret=admin_secret,
            name=getattr(args, "name", None),
            description=getattr(args, "description", None),
        )
    except ProjectRegistrationError as e:
        print(f"agent-trace project create: {e}", file=sys.stderr)
        sys.exit(1 if e.status != 409 else 2)

    print(json.dumps(proj, indent=2))


# ===================================================================
# remote
# ===================================================================

def _resolve_pid_for_remote():
    from .storage import resolve_project_id
    pid = resolve_project_id(os.getcwd(), create=False)
    if not pid:
        print("agent-trace: not in an initialised project. Run 'agent-trace init' first.", file=sys.stderr)
        sys.exit(1)
    return pid


def cmd_remote(args):
    """Manage named remotes (git remote-like)."""
    action = getattr(args, "remote_action", None)

    if action == "add":
        pid = _resolve_pid_for_remote()
        url = args.url
        token_value = getattr(args, "token", None)
        token_env = getattr(args, "token_env", None)

        # Parse + scope-check up front, regardless of --create. We refuse to
        # bind a remote whose URL points at one org while the token belongs
        # to another — local config and server state would silently diverge.
        try:
            base_url, url_org_slug, url_project_slug = parse_remote_url(url)
        except RemoteUrlError as e:
            print(f"agent-trace remote add: {e}", file=sys.stderr)
            sys.exit(1)

        resolved_token = token_value
        if resolved_token is None and token_env:
            resolved_token = os.environ.get(token_env)

        if resolved_token:
            try:
                assert_token_matches_url(
                    base_url, resolved_token,
                    expected_org_slug=url_org_slug,
                    expected_project_slug=url_project_slug,
                )
            except TokenScopeError as e:
                print(f"agent-trace remote add: {e}", file=sys.stderr)
                sys.exit(1)

        if getattr(args, "create", False):
            admin_secret = os.environ.get("AGENT_TRACE_ADMIN_SECRET")
            if not resolved_token and not admin_secret:
                print(
                    "agent-trace remote add --create: a token is required to register the "
                    "project. Pass --token, --token-env, or set AGENT_TRACE_ADMIN_SECRET.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                proj = register_project_via_remote(
                    base_url, url_project_slug,
                    org_slug=url_org_slug,
                    token=resolved_token, admin_secret=admin_secret,
                )
                print(f"Registered project '{proj['project_id']}' on {base_url}")
            except ProjectRegistrationError as e:
                if e.status == 409:
                    print(f"Project '{url_project_slug}' already exists; binding the remote anyway.")
                else:
                    print(f"agent-trace remote add --create: {e}", file=sys.stderr)
                    sys.exit(1)

        try:
            entry = remote_add(
                pid, args.name, url,
                token=token_value, token_env=token_env,
            )
            print(f"Remote '{args.name}' added: {entry['url']}")
        except ValueError as e:
            print(f"agent-trace remote add: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "list":
        pid = _resolve_pid_for_remote()
        remotes = remote_list(pid)
        if not remotes:
            print("No remotes configured. Run 'agent-trace remote add <name> <url>'.")
            return
        for r in remotes:
            ref = r["token_ref"] or "(no auth)"
            print(f"  {r['name']:<15} {r['url']}  (token: {ref})")

    elif action == "show":
        pid = _resolve_pid_for_remote()
        info = remote_show(pid, args.name)
        if not info:
            print(f"Remote '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)
        print(f"  Name:     {info['name']}")
        print(f"  URL:      {info['url']}")
        print(f"  Host:     {info['base_url']}")
        print(f"  Org:      {info['org_slug']}")
        print(f"  Project:  {info['project_slug']}")
        print(f"  Auth:     {info['auth_type']}")
        print(f"  Token:    {info['token_masked']}  (ref: {info['token_ref']})")

    elif action == "set-url":
        pid = _resolve_pid_for_remote()
        # Validate the new URL's slugs against whatever token the remote
        # already holds. Same rationale as `remote add`: a URL change must
        # not leave the local remote pointing at one org while the token
        # belongs to another.
        try:
            base_url, url_org_slug, url_project_slug = parse_remote_url(args.url)
        except RemoteUrlError as e:
            print(f"agent-trace remote set-url: {e}", file=sys.stderr)
            sys.exit(1)
        from .remote import get_remote as _get_remote, get_remote_token as _get_token
        existing = _get_remote(pid, args.name)
        if existing is None:
            print(f"agent-trace remote set-url: Remote '{args.name}' does not exist.", file=sys.stderr)
            sys.exit(1)
        token_value = _get_token(existing)
        if token_value:
            try:
                assert_token_matches_url(
                    base_url, token_value,
                    expected_org_slug=url_org_slug,
                    expected_project_slug=url_project_slug,
                )
            except TokenScopeError as e:
                print(f"agent-trace remote set-url: {e}", file=sys.stderr)
                sys.exit(1)
        try:
            set_remote_url(pid, args.name, args.url)
            print(f"Remote '{args.name}' URL updated.")
        except ValueError as e:
            print(f"agent-trace remote set-url: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "set-token":
        pid = _resolve_pid_for_remote()
        # Validate the new token against the remote's current URL before we
        # persist it, so the user catches an org/project mismatch up front
        # rather than at the next sync.
        from .remote import (
            get_remote as _get_remote,
            get_remote_base_url as _get_base,
            get_remote_org_slug as _get_org,
            get_remote_project_slug as _get_proj,
        )
        existing = _get_remote(pid, args.name)
        if existing is None:
            print(f"agent-trace remote set-token: Remote '{args.name}' does not exist.", file=sys.stderr)
            sys.exit(1)
        new_token = getattr(args, "token", None)
        new_token_env = getattr(args, "token_env", None)
        resolved_new_token = new_token
        if resolved_new_token is None and new_token_env:
            resolved_new_token = os.environ.get(new_token_env)
        base_url = _get_base(existing)
        url_org_slug = _get_org(existing)
        url_project_slug = _get_proj(existing)
        if resolved_new_token and base_url and url_org_slug:
            try:
                assert_token_matches_url(
                    base_url, resolved_new_token,
                    expected_org_slug=url_org_slug,
                    expected_project_slug=url_project_slug,
                )
            except TokenScopeError as e:
                print(f"agent-trace remote set-token: {e}", file=sys.stderr)
                sys.exit(1)
        try:
            set_remote_token(
                pid, args.name,
                token=new_token,
                token_env=new_token_env,
            )
            print(f"Remote '{args.name}' token updated.")
        except ValueError as e:
            print(f"agent-trace remote set-token: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "remove":
        pid = _resolve_pid_for_remote()
        if remote_remove(pid, args.name):
            print(f"Remote '{args.name}' removed.")
        else:
            print(f"Remote '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)

    elif action == "rename":
        pid = _resolve_pid_for_remote()
        try:
            remote_rename(pid, args.old_name, args.new_name)
            print(f"Remote '{args.old_name}' renamed to '{args.new_name}'.")
        except ValueError as e:
            print(f"agent-trace remote rename: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "default":
        pid = _resolve_pid_for_remote()
        try:
            set_default_remote(pid, args.name)
            print(f"Default remote set to '{args.name}'.")
        except ValueError as e:
            print(f"agent-trace remote default: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: agent-trace remote {add,list,show,set-url,set-token,remove,rename,default}")


# ===================================================================
# push / pull / sync
# ===================================================================

def cmd_push(args):
    """Push local data to a remote service."""
    pid = _resolve_pid_for_remote()
    try:
        result = sync_push(
            pid,
            remote_name=getattr(args, "remote", None),
            full=getattr(args, "full", False),
            only=getattr(args, "only", None),
            since=getattr(args, "since", None),
            dry_run=getattr(args, "dry_run", False),
        )
    except ValueError as e:
        print(f"agent-trace push: {e}", file=sys.stderr)
        sys.exit(1)

    prefix = "[dry-run] " if result.dry_run else ""
    print(f"{prefix}Pushed {result.traces_pushed} trace(s), "
          f"{result.ledgers_pushed} ledger(s), "
          f"{result.commit_links_pushed} commit-link(s), "
          f"{result.conversations_pushed} conversation(s), "
          f"{result.summaries_pushed} summary(ies)")
    if result.traces_held_back:
        print(f"  ({result.traces_held_back} unattributed trace(s) held back; "
              "use --full to push)")
    for err in result.errors:
        print(f"  Error: {err}", file=sys.stderr)


def cmd_pull(args):
    """Pull remote data into local storage."""
    pid = _resolve_pid_for_remote()
    try:
        result = sync_pull(
            pid,
            remote_name=getattr(args, "remote", None),
            since=getattr(args, "since", None),
            dry_run=getattr(args, "dry_run", False),
        )
    except ValueError as e:
        print(f"agent-trace pull: {e}", file=sys.stderr)
        sys.exit(1)

    prefix = "[dry-run] " if result.dry_run else ""
    print(f"{prefix}Pulled {result.traces_pulled} trace(s), "
          f"{result.ledgers_pulled} ledger(s), "
          f"{result.commit_links_pulled} commit-link(s), "
          f"{result.conversations_pulled} conversation(s), "
          f"{result.summaries_pulled} summary(ies)")
    for err in result.errors:
        print(f"  Error: {err}", file=sys.stderr)


def cmd_sync(args):
    """Push + pull in one go."""
    pid = _resolve_pid_for_remote()
    rn = getattr(args, "remote", None)
    try:
        push_r = sync_push(pid, remote_name=rn)
    except ValueError as e:
        print(f"agent-trace sync (push): {e}", file=sys.stderr)
        push_r = None
    try:
        pull_r = sync_pull(pid, remote_name=rn)
    except ValueError as e:
        print(f"agent-trace sync (pull): {e}", file=sys.stderr)
        pull_r = None

    if push_r:
        print(f"Pushed {push_r.traces_pushed} trace(s), "
              f"{push_r.ledgers_pushed} ledger(s), "
              f"{push_r.commit_links_pushed} commit-link(s), "
              f"{push_r.conversations_pushed} conversation(s), "
              f"{push_r.summaries_pushed} summary(ies)")
    if pull_r:
        print(f"Pulled {pull_r.traces_pulled} trace(s), "
              f"{pull_r.ledgers_pulled} ledger(s), "
              f"{pull_r.commit_links_pulled} commit-link(s), "
              f"{pull_r.conversations_pulled} conversation(s), "
              f"{pull_r.summaries_pulled} summary(ies)")


# ===================================================================
# notes (git refs/notes/agent-trace)
# ===================================================================


def _resolve_note_section_flags(
    args,
    cwd: str,
) -> tuple[bool, bool, bool, bool]:
    """Merge ``--include-*`` / ``--no-include-*`` with project ``notes.*`` defaults.

    Returns ``(include_ledger, include_summary, include_prompts,
    include_all_session_conversations)``.
    """
    from .git_notes import all_session_conversations_enabled, project_notes_flags

    d_l, d_s, d_p = project_notes_flags(cwd)
    d_asc = all_session_conversations_enabled(cwd)

    def one(name: str, default: bool) -> bool:
        t = getattr(args, f"include_{name}", False)
        f = getattr(args, f"no_include_{name}", False)
        if t and f:
            print(
                f"agent-trace notes: specify only one of --include-{name} or --no-include-{name}",
                file=sys.stderr,
            )
            sys.exit(1)
        if t:
            return True
        if f:
            return False
        return default

    return (
        one("ledger", d_l),
        one("summary", d_s),
        one("prompts", d_p),
        one("all_session_conversations", d_asc),
    )


def cmd_summary(args):
    """Enable/disable/configure pluggable session summaries."""
    from .storage import resolve_project_id
    from .summary import get_summary_for_commit, run_summary_generate
    from .git_notes import resolve_commit

    cwd = os.getcwd()
    action = getattr(args, "summary_action", None)

    if action == "enable":
        cfg = get_project_config(cwd)
        if cfg is None:
            print("agent-trace: project not initialised (run agent-trace init)", file=sys.stderr)
            sys.exit(1)
        # Use ``summary_command`` dest so we do not overwrite the top-level ``command`` (subcommand name).
        cmd = getattr(args, "summary_command", None) or ""
        if not cmd.strip():
            print("agent-trace summary enable: --command is required", file=sys.stderr)
            sys.exit(1)
        cfg.setdefault("summary", {})
        cfg["summary"]["enabled"] = True
        cfg["summary"]["command"] = cmd
        to = getattr(args, "summary_timeout", None)
        if to is not None:
            cfg["summary"]["timeout_seconds"] = int(to)
        save_project_config(cfg, cwd)
        print("Session summaries enabled.")
        return

    if action == "presets":
        rows = list_summary_presets()
        print("Built-in summary presets:\n")
        for row in rows:
            alias = row["alias"]
            desc = row["description"]
            if row.get("needs_model"):
                dm = row.get("default_model") or DEFAULT_OLLAMA_MODEL
                print(f"  {alias:<16} {desc} (model required; default: {dm})")
            else:
                print(f"  {alias:<16} {desc}")
        print()
        print("Configure one with:")
        print("  agent-trace summary use <preset> [--model <name>] [--timeout <seconds>]")
        return

    if action == "use":
        cfg = get_project_config(cwd)
        if cfg is None:
            print("agent-trace: project not initialised (run agent-trace init)", file=sys.stderr)
            sys.exit(1)
        alias = (getattr(args, "preset_alias", None) or "").strip()
        if alias not in PRESET_ALIASES:
            print(
                f"agent-trace summary use: unknown preset '{alias}'. "
                "Run 'agent-trace summary presets'.",
                file=sys.stderr,
            )
            sys.exit(1)
        model = getattr(args, "model", None)
        if alias != "ollama-summary" and model:
            print(
                "agent-trace summary use: --model is only valid with ollama-summary",
                file=sys.stderr,
            )
            sys.exit(1)
        command = build_preset_command(alias, model=model)
        cfg.setdefault("summary", {})
        cfg["summary"]["enabled"] = True
        cfg["summary"]["command"] = command
        to = getattr(args, "summary_timeout", None)
        if to is not None:
            cfg["summary"]["timeout_seconds"] = int(to)
        save_project_config(cfg, cwd)
        print(f"Session summaries enabled using preset '{alias}'.")
        print(f"summary.command = {command}")
        return

    if action == "preset-run":
        alias = (getattr(args, "preset_alias", None) or "").strip()
        model = getattr(args, "model", None)
        transcript_text = sys.stdin.read()
        code = run_summary_preset(alias, transcript_text, model=model)
        if code != 0:
            print(
                "agent-trace summary preset-run: failed "
                f"(preset={alias}, ensure required tool is installed and authenticated)",
                file=sys.stderr,
            )
            sys.exit(code)
        return

    if action == "disable":
        cfg = get_project_config(cwd)
        if cfg is None:
            print("agent-trace: project not initialised", file=sys.stderr)
            sys.exit(1)
        cfg.setdefault("summary", {})
        cfg["summary"]["enabled"] = False
        save_project_config(cfg, cwd)
        print("Session summaries disabled.")
        return

    if action == "generate":
        cid = (getattr(args, "conversation_id", None) or "").strip()
        sid = (getattr(args, "session_id", None) or "").strip()
        if not (cid or sid):
            print(
                "agent-trace summary generate: --conversation ID or --session-id SID required",
                file=sys.stderr,
            )
            sys.exit(1)
        out = run_summary_generate(
            cwd,
            conversation_id=cid or None,
            session_id=sid or None,
        )
        if out is None:
            print("agent-trace summary generate: failed or no transcript", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(out, indent=2))
        return

    if action == "show":
        rev = getattr(args, "commit", None) or "HEAD"
        sha = resolve_commit(cwd, rev)
        if not sha:
            print(f"agent-trace summary show: bad revision: {rev}", file=sys.stderr)
            sys.exit(1)
        pid = resolve_project_id(cwd, create=False)
        if not pid:
            print("agent-trace summary show: no project id", file=sys.stderr)
            sys.exit(1)
        s = get_summary_for_commit(pid, sha)
        if not s:
            print(f"No summaries stored for {sha}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(s, indent=2))
        return


def cmd_notes(args):
    """Manage per-commit JSON notes on ``refs/notes/agent-trace``."""
    from .git_notes import (
        attach_note,
        backfill_notes,
        build_note,
        git_notes_pull,
        git_notes_push,
        read_note,
        rebuild_notes_for_range,
        resolve_commit,
        strip_sections,
    )
    from .ledger import load_local_ledgers

    cwd = os.getcwd()
    action = getattr(args, "notes_action", None)

    if action == "show":
        rev = getattr(args, "commit", None) or "HEAD"
        sha = resolve_commit(cwd, rev)
        if not sha:
            print(f"agent-trace notes show: bad revision: {rev}", file=sys.stderr)
            sys.exit(1)
        note = read_note(sha, cwd)
        if not note:
            print(f"No agent-trace note for {sha}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(note, indent=2))
        return

    if action == "attach":
        rev = getattr(args, "commit", None) or "HEAD"
        sha = resolve_commit(cwd, rev)
        if not sha:
            print(f"agent-trace notes attach: bad revision: {rev}", file=sys.stderr)
            sys.exit(1)
        ledgers = load_local_ledgers(cwd)
        led = ledgers.get(sha)
        if not led:
            print(
                "agent-trace notes attach: no local ledger for this commit "
                "(build a ledger first, e.g. make a commit with agent-trace hooks).",
                file=sys.stderr,
            )
            sys.exit(1)
        il, isum, ipr, iasc = _resolve_note_section_flags(args, cwd)
        from .git_notes import load_traces_for_ids

        tid_list = [str(x) for x in led.get("trace_ids", [])]
        traces = load_traces_for_ids(cwd, tid_list)
        summaries = None
        if isum:
            from .summary import merge_note_summaries

            cfg = get_project_config(cwd) or {}
            nc = cfg.get("notes") or {}
            static_s = nc.get("summaries") if isinstance(nc.get("summaries"), dict) else None
            summaries = merge_note_summaries(cwd, led, static_s)
        asc = None
        if iasc:
            from .summary import all_session_conversations_for_ledger

            asc = all_session_conversations_for_ledger(cwd, led)
        note = build_note(
            led,
            traces,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            summaries=summaries,
            include_all_session_conversations=iasc,
            all_session_conversations=asc,
        )
        if attach_note(sha, note, cwd):
            print(f"Attached note to {sha}")
        else:
            print("agent-trace notes attach: failed", file=sys.stderr)
            sys.exit(1)
        return

    if action == "rebuild":
        range_spec = getattr(args, "range_spec", None)
        if not range_spec:
            print("agent-trace notes rebuild: <range> required (e.g. HEAD~10..HEAD)", file=sys.stderr)
            sys.exit(1)
        il, isum, ipr, iasc = _resolve_note_section_flags(args, cwd)
        n = rebuild_notes_for_range(
            cwd,
            range_spec,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            include_all_session_conversations=iasc,
        )
        print(f"Rebuilt notes for {n} commit(s)")
        return

    if action == "backfill":
        il, isum, ipr, iasc = _resolve_note_section_flags(args, cwd)
        since = getattr(args, "since", None)
        n = backfill_notes(
            cwd,
            since=since,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            include_all_session_conversations=iasc,
        )
        print(f"Backfilled notes for {n} commit(s)")
        return

    if action == "strip":
        rev = getattr(args, "commit", None) or "HEAD"
        sha = resolve_commit(cwd, rev)
        if not sha:
            print(f"agent-trace notes strip: bad revision: {rev}", file=sys.stderr)
            sys.exit(1)
        sections: list[str] = []
        if getattr(args, "strip_ledger", False):
            sections.append("ledger")
        if getattr(args, "strip_summary", False):
            sections.append("summary")
        if getattr(args, "strip_prompts", False):
            sections.append("prompts")
        if getattr(args, "strip_all_session_conversations", False):
            sections.append("all_session_conversations")
        if not sections:
            print(
                "agent-trace notes strip: specify at least one of "
                "--ledger --summary --prompts --all-session-conversations",
                file=sys.stderr,
            )
            sys.exit(1)
        if strip_sections(sha, sections, cwd):
            print(f"Stripped {', '.join(sections)} from note on {sha}")
        else:
            print("agent-trace notes strip: no note or failed", file=sys.stderr)
            sys.exit(1)
        return

    if action == "push":
        remote = getattr(args, "remote", None) or "origin"
        ok, msg = git_notes_push(cwd, remote=remote)
        if ok:
            print(msg or "push ok")
        else:
            print(f"agent-trace notes push: {msg}", file=sys.stderr)
            sys.exit(1)
        return

    if action == "pull":
        remote = getattr(args, "remote", None) or "origin"
        ok, msg = git_notes_pull(cwd, remote=remote)
        if ok:
            print(msg or "fetch ok")
        else:
            print(f"agent-trace notes pull: {msg}", file=sys.stderr)
            sys.exit(1)
        return


# ===================================================================
# telemetry (global flag)
# ===================================================================


def cmd_telemetry_control(args) -> None:
    """Handle ``agent-trace --telemetry on|off|status`` (no subcommand)."""
    mode = args.telemetry
    if mode == "on":
        set_telemetry_enabled(True)
        print("Anonymous telemetry is now enabled (see docs/concepts/telemetry.md).")
        return
    if mode == "off":
        set_telemetry_enabled(False)
        print("Telemetry is now disabled.")
        return
    for line in telemetry_status_lines():
        print(line)


# ===================================================================
# Entry point
# ===================================================================

def _run_subcommand(args, set_p, rm_p) -> None:
    """Run one subcommand; may raise ``SystemExit``."""
    dispatch = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "status": cmd_status,
        "reset": cmd_reset,
        "config": cmd_config,
        "hooks": cmd_hooks,
        "record": cmd_record,
        "commit-link": cmd_commit_link,
        "rewrite-ledger": cmd_rewrite_ledger,
        "viewer": cmd_viewer,
        "blame": cmd_blame,
        "context": cmd_context,
        "projects": cmd_projects,
        "adopt": cmd_adopt,
        "project": cmd_project,
        "push": cmd_push,
        "pull": cmd_pull,
        "sync": cmd_sync,
        "notes": cmd_notes,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
    elif args.command == "remote":
        cmd_remote(args)
    elif args.command == "rule":
        cmd_rule(args)
    elif args.command == "set":
        if getattr(args, "set_command", None) == "globaluser":
            cmd_set_globaluser(args)
        else:
            set_p.print_help()
    elif args.command == "remove":
        if getattr(args, "remove_command", None) == "globaluser":
            cmd_remove_globaluser(args)
        else:
            rm_p.print_help()
    elif args.command == "summary":
        cmd_summary(args)


def main():
    parser = argparse.ArgumentParser(
        prog="agent-trace",
        description="agent-trace — AI code tracing tool",
    )
    parser.add_argument(
        "--version", action="version", version=f"agent-trace {VERSION}",
    )
    parser.add_argument(
        "--telemetry",
        choices=["on", "off", "status"],
        default=None,
        metavar="MODE",
        help="opt-in anonymous telemetry: on, off, or status (no subcommand; default off)",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("init", help="Initialize agent-trace for the current project")

    doc_p = sub.add_parser("doctor", help="Check hooks, config, remotes, and optional tools")
    doc_p.add_argument(
        "--fix",
        action="store_true",
        help="Apply safe automatic fixes (permissions, init, hooks, git notes refspec)",
    )
    doc_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="With --fix, show what would change without modifying the system",
    )
    doc_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="With --fix, apply fixes non-interactively (e.g. run init without prompting)",
    )
    sub.add_parser("status", help="Show agent-trace status")
    sub.add_parser("reset", help="Reset agent-trace configuration")
    sub_config = sub.add_parser("config", help="Show or update persisted configuration")
    config_sub = sub_config.add_subparsers(dest="config_action", metavar="ACTION", required=True)
    c_show = config_sub.add_parser("show", help="Show full persisted configuration")
    c_show.add_argument("--json", action="store_true", default=False, help="Output as JSON")
    c_set = config_sub.add_parser("set", help="Set one configuration field")
    c_set.add_argument("field", choices=sorted(_CONFIG_FIELDS), help="Config field to update")
    c_set.add_argument("value", help="New value")
    c_reset = config_sub.add_parser("reset", help="Reset one configuration field or group")
    c_reset.add_argument("field", choices=sorted(_CONFIG_RESET_TARGETS), help="Config field/group to reset")
    c_reset.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Reset directly without interactive prompt",
    )

    # hooks {setup-global, remove-global, status}
    hook_choices = _hook_tool_choices()
    hook_choices_help = ", ".join(hook_choices)
    sub_hooks = sub.add_parser(
        "hooks",
        help=f"Manage global hooks for coding tools ({hook_choices_help})",
    )
    hooks_sub = sub_hooks.add_subparsers(dest="hooks_action", metavar="ACTION")
    h_setup = hooks_sub.add_parser(
        "setup-global",
        help="Install global hooks for one or more registered coding-agent adapters",
    )
    h_setup.add_argument("--tool", "-t", dest="tools", action="append", choices=hook_choices,
                         help="Tool(s) to configure (default: all). Can be repeated.")
    h_remove = hooks_sub.add_parser("remove-global", help="Remove global hooks")
    h_remove.add_argument("--tool", "-t", dest="tools", action="append", choices=hook_choices,
                          help="Tool(s) to remove (default: all). Can be repeated.")
    hooks_sub.add_parser("status", help="Show global hook status")

    sub.add_parser("record", help="Record a trace from stdin (used by hooks)")
    sub.add_parser("commit-link", help="Link current commit to traces (called by git hook)")
    sub.add_parser("rewrite-ledger", help="Remap ledgers after rebase/amend (called by git hook)")

    # viewer [--project /path]
    sub_viewer = sub.add_parser("viewer", help="Open the file viewer (browse files, git + agent-trace blame)")
    sub_viewer.add_argument("--project", "-p", default=None, help="Project directory (default: current directory)")

    # blame <file>
    sub_blame = sub.add_parser("blame", help="Show AI attribution for a file")
    sub_blame.add_argument("file", help="File path to blame")
    sub_blame.add_argument("--line", "-l", type=int, default=None,
                           help="Specific line number")
    sub_blame.add_argument("--range", "-r", default=None,
                           help="Line range (e.g. 10-25)")
    sub_blame.add_argument(
        "--project", "-p", default=None,
        help="Git repo root path or registry project_id (disambiguate multi-repo cwd)",
    )
    sub_blame.add_argument("--json", action="store_true", default=False,
                           help="Output as JSON")
    sub_blame.add_argument(
        "--show-no-attribution",
        action="store_true",
        default=False,
        help="Include lines not attributed to AI (NO_ATTRIBUTION); default is to omit them",
    )
    sub_blame.add_argument(
        "--require-attribution",
        action="store_true",
        default=False,
        help="Exit with non-zero status if any line is NO_ATTRIBUTION (for CI)",
    )

    # context <file>
    sub_context = sub.add_parser("context", help="Get conversation context for AI-attributed code")
    sub_context.add_argument("file", help="File path to get context for")
    sub_context.add_argument("--lines", "-l", default=None,
                             help="Line range (e.g. 10-25)")
    sub_context.add_argument(
        "--project", "-p", default=None,
        help="Git repo root path or registry project_id (disambiguate multi-repo cwd)",
    )
    sub_context.add_argument("--full", action="store_true", default=False,
                             help="Include full conversation transcript")
    sub_context.add_argument("--json", action="store_true", default=False,
                             help="Output as JSON (for machine consumption)")
    sub_context.add_argument("--query", "-q", default=None,
                             help="Query to pass through for subagent instruction")

    # rule {add,remove,show,list}
    sub_rule = sub.add_parser("rule", help="Manage agent rules for coding agents")
    rule_sub = sub_rule.add_subparsers(dest="rule_action", metavar="ACTION")

    rule_tool_choices = list(TOOL_CHOICES)
    rule_tool_help = " | ".join(rule_tool_choices) or "(no tools registered)"

    # rule add <name> --tool <...>
    rule_add = rule_sub.add_parser("add", help="Add a prebuilt rule")
    rule_add.add_argument("rule_name", help="Rule name (e.g. context-for-agents)")
    rule_add.add_argument("--tool", "-t", required=True, choices=rule_tool_choices,
                          help=f"Tool to add the rule for ({rule_tool_help})")

    # rule remove <name> --tool <...>
    rule_rm = rule_sub.add_parser("remove", help="Remove a rule")
    rule_rm.add_argument("rule_name", help="Rule name (e.g. context-for-agents)")
    rule_rm.add_argument("--tool", "-t", required=True, choices=rule_tool_choices,
                         help=f"Tool to remove the rule from ({rule_tool_help})")

    # rule show
    rule_sub.add_parser("show", help="Show which rules are configured")

    # rule list
    rule_sub.add_parser("list", help="List available prebuilt rules")

    # set globaluser <token>
    set_p = sub.add_parser("set", help="Set global configuration")
    set_sub = set_p.add_subparsers(dest="set_command", metavar="KEY")
    gu = set_sub.add_parser("globaluser", help="Set global auth token")
    gu.add_argument("token", help="The auth token to store globally")

    # remove globaluser
    rm_p = sub.add_parser("remove", help="Remove global configuration")
    rm_sub = rm_p.add_subparsers(dest="remove_command", metavar="KEY")
    rm_sub.add_parser("globaluser", help="Remove global auth token")

    # remote {add,list,show,set-url,set-token,remove,rename,default}
    sub_remote = sub.add_parser("remote", help="Manage named remotes (git remote-like)")
    remote_sub = sub_remote.add_subparsers(dest="remote_action", metavar="ACTION")

    r_add = remote_sub.add_parser("add", help="Add a remote")
    r_add.add_argument("name", help="Remote name (e.g. origin)")
    r_add.add_argument("url", help="Remote URL: <scheme>://<host>/<org>/<project>")
    r_add.add_argument("--token", default=None, help="Auth token (stored globally)")
    r_add.add_argument("--token-env", default=None, help="Environment variable holding the token")
    r_add.add_argument(
        "--create", action="store_true", default=False,
        help="Register the project on the remote service before storing the remote",
    )

    r_list = remote_sub.add_parser("list", help="List remotes")

    r_show = remote_sub.add_parser("show", help="Show remote details (token masked)")
    r_show.add_argument("name", help="Remote name")

    r_seturl = remote_sub.add_parser("set-url", help="Change remote URL")
    r_seturl.add_argument("name", help="Remote name")
    r_seturl.add_argument("url", help="New URL")

    r_settok = remote_sub.add_parser("set-token", help="Update remote auth token")
    r_settok.add_argument("name", help="Remote name")
    r_settok.add_argument("--token", default=None, help="New auth token")
    r_settok.add_argument("--token-env", default=None, help="Environment variable holding the token")

    r_rm = remote_sub.add_parser("remove", help="Remove a remote")
    r_rm.add_argument("name", help="Remote name")

    r_ren = remote_sub.add_parser("rename", help="Rename a remote")
    r_ren.add_argument("old_name", help="Current name")
    r_ren.add_argument("new_name", help="New name")

    r_def = remote_sub.add_parser("default", help="Set default remote")
    r_def.add_argument("name", help="Remote name to set as default")

    # push
    sub_push = sub.add_parser("push", help="Push local data to a remote")
    sub_push.add_argument("--remote", default=None, help="Remote name (default: auto)")
    sub_push.add_argument("--full", action="store_true", default=False,
                          help="Include unattributed traces (default: attributed only)")
    sub_push.add_argument("--only", default=None, choices=["traces", "ledgers", "commit-links"],
                          help="Push only one artifact type")
    sub_push.add_argument("--since", default=None, help="Push only since this timestamp/commit")
    sub_push.add_argument("--dry-run", action="store_true", default=False,
                          help="Show what would be pushed without sending")

    # pull
    sub_pull = sub.add_parser("pull", help="Pull remote data into local storage")
    sub_pull.add_argument("--remote", default=None, help="Remote name (default: auto)")
    sub_pull.add_argument("--since", default=None, help="Pull only since this timestamp")
    sub_pull.add_argument("--dry-run", action="store_true", default=False,
                          help="Show what would be pulled without writing")

    # sync
    sub_sync = sub.add_parser("sync", help="Push + pull in one go")
    sub_sync.add_argument("--remote", default=None, help="Remote name (default: auto)")

    p_projects = sub.add_parser("projects", help="List registered projects (or: projects show <id>)")
    p_projects.add_argument("projects_args", nargs="*", default=[], help="show <project_id>")

    p_adopt = sub.add_parser("adopt", help="Register a repo and print its project_id")
    p_adopt.add_argument(
        "adopt_path",
        nargs="?",
        default=".",
        help="Path to repository (default: current directory)",
    )

    # project {create}
    p_project = sub.add_parser("project", help="Server-side project administration")
    project_sub = p_project.add_subparsers(dest="project_action", metavar="ACTION")

    p_proj_create = project_sub.add_parser(
        "create",
        help="Register a project on a remote service (POST /api/v1/projects)",
    )
    p_proj_create.add_argument(
        "url",
        help="Remote URL: <scheme>://<host>/<org>/<project>",
    )
    p_proj_create.add_argument("--token", default=None, help="Org-scoped token (with projects:write)")
    p_proj_create.add_argument(
        "--token-env", default=None,
        help="Environment variable holding the token",
    )
    p_proj_create.add_argument("--name", default=None, help="Optional human-readable display name")
    p_proj_create.add_argument("--description", default=None, help="Optional description")

    # notes {show, attach, rebuild, backfill, strip, push, pull}
    sub_notes = sub.add_parser("notes", help="Git notes (refs/notes/agent-trace)")
    notes_sub = sub_notes.add_subparsers(dest="notes_action", metavar="ACTION", required=True)

    ns_show = notes_sub.add_parser("show", help="Print JSON note for a commit")
    ns_show.add_argument("commit", nargs="?", default="HEAD", help="Commit (default: HEAD)")

    ns_attach = notes_sub.add_parser("attach", help="Build note from local ledger and attach")
    ns_attach.add_argument("commit", nargs="?", default="HEAD", help="Commit (default: HEAD)")
    ns_attach.add_argument("--include-ledger", action="store_true", help="Include ledger section")
    ns_attach.add_argument("--no-include-ledger", action="store_true", help="Omit ledger section")
    ns_attach.add_argument("--include-summary", action="store_true")
    ns_attach.add_argument("--no-include-summary", action="store_true")
    ns_attach.add_argument("--include-prompts", action="store_true")
    ns_attach.add_argument("--no-include-prompts", action="store_true")
    ns_attach.add_argument(
        "--include-all-session-conversations",
        action="store_true",
        help="Include all_session_conversations section (staging window)",
    )
    ns_attach.add_argument(
        "--no-include-all-session-conversations",
        action="store_true",
        help="Omit all_session_conversations section",
    )

    ns_rebuild = notes_sub.add_parser("rebuild", help="Rebuild notes from local ledgers for a commit range")
    ns_rebuild.add_argument("range_spec", help="Range for git rev-list (e.g. HEAD~10..HEAD)")
    ns_rebuild.add_argument("--include-ledger", action="store_true")
    ns_rebuild.add_argument("--no-include-ledger", action="store_true")
    ns_rebuild.add_argument("--include-summary", action="store_true")
    ns_rebuild.add_argument("--no-include-summary", action="store_true")
    ns_rebuild.add_argument("--include-prompts", action="store_true")
    ns_rebuild.add_argument("--no-include-prompts", action="store_true")
    ns_rebuild.add_argument("--include-all-session-conversations", action="store_true")
    ns_rebuild.add_argument("--no-include-all-session-conversations", action="store_true")

    ns_backfill = notes_sub.add_parser("backfill", help="Rebuild notes for commits (optional --since)")
    ns_backfill.add_argument("--since", default=None, help="git rev-list --since (e.g. 2026-01-01)")
    ns_backfill.add_argument("--include-ledger", action="store_true")
    ns_backfill.add_argument("--no-include-ledger", action="store_true")
    ns_backfill.add_argument("--include-summary", action="store_true")
    ns_backfill.add_argument("--no-include-summary", action="store_true")
    ns_backfill.add_argument("--include-prompts", action="store_true")
    ns_backfill.add_argument("--no-include-prompts", action="store_true")
    ns_backfill.add_argument("--include-all-session-conversations", action="store_true")
    ns_backfill.add_argument("--no-include-all-session-conversations", action="store_true")

    ns_strip = notes_sub.add_parser("strip", help="Remove optional sections from a note")
    ns_strip.add_argument("commit", nargs="?", default="HEAD")
    ns_strip.add_argument("--ledger", dest="strip_ledger", action="store_true")
    ns_strip.add_argument("--summary", dest="strip_summary", action="store_true")
    ns_strip.add_argument("--prompts", dest="strip_prompts", action="store_true")
    ns_strip.add_argument(
        "--all-session-conversations",
        dest="strip_all_session_conversations",
        action="store_true",
    )

    ns_npush = notes_sub.add_parser("push", help="Push refs/notes/agent-trace to a remote")
    ns_npush.add_argument("--remote", default="origin")

    ns_npull = notes_sub.add_parser("pull", help="Fetch refs/notes/agent-trace from a remote")
    ns_npull.add_argument("--remote", default="origin")

    sub_summary = sub.add_parser(
        "summary",
        help="Pluggable transcript summaries (command reads raw transcript on stdin, prints summary on stdout)",
    )
    sum_sub = sub_summary.add_subparsers(dest="summary_action", metavar="ACTION", required=True)
    s_en = sum_sub.add_parser("enable", help="Enable and set the summary command")
    s_en.add_argument(
        "--command",
        dest="summary_command",
        required=True,
        help="Executable: raw transcript on stdin, summary text on stdout",
    )
    s_en.add_argument(
        "--timeout",
        dest="summary_timeout",
        type=int,
        default=None,
        help="Timeout seconds (default 30)",
    )
    sum_sub.add_parser("presets", help="List built-in summary presets")
    s_use = sum_sub.add_parser("use", help="Enable a built-in summary preset")
    s_use.add_argument("preset_alias", choices=PRESET_ALIASES, help="Preset alias")
    s_use.add_argument(
        "--model",
        default=None,
        help="Model name for ollama-summary (ignored by other presets)",
    )
    s_use.add_argument(
        "--timeout",
        dest="summary_timeout",
        type=int,
        default=None,
        help="Timeout seconds (default 30)",
    )
    s_pr = sum_sub.add_parser(
        "preset-run",
        help=argparse.SUPPRESS,  # internal: summary.command target for built-in presets
    )
    s_pr.add_argument("preset_alias", choices=PRESET_ALIASES)
    s_pr.add_argument("--model", default=None)
    sum_sub.add_parser("disable", help="Disable session-end summaries")
    s_gen = sum_sub.add_parser(
        "generate",
        help="Re-run summary for a conversation id or every id in a session",
    )
    s_gen.add_argument(
        "--conversation",
        dest="conversation_id",
        default=None,
        help="A specific conversation_id (64-hex sha256 over the original transcript URL)",
    )
    s_gen.add_argument(
        "--session-id",
        dest="session_id",
        default=None,
        help="session_id; regenerates every conversation id referenced by traces in that session",
    )
    s_show = sum_sub.add_parser(
        "show",
        help="Show {conversation_id: summary} for a commit",
    )
    s_show.add_argument("commit", nargs="?", default="HEAD", help="Commit (default HEAD)")

    args = parser.parse_args()

    if getattr(args, "telemetry", None) is not None:
        if args.command is not None:
            parser.error(
                "--telemetry cannot be combined with a subcommand "
                "(example: agent-trace --telemetry status)",
            )
        cmd_telemetry_control(args)
        return

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    start = time.monotonic()
    try:
        _run_subcommand(args, set_p, rm_p)
    except SystemExit as exc:
        code = exc.code
        exit_code = (
            0
            if code is None
            else (code if isinstance(code, int) else (1 if code else 0))
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        maybe_report_cli_run(
            version=VERSION,
            command=args.command,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )
        raise
    except BaseException:
        duration_ms = int((time.monotonic() - start) * 1000)
        maybe_report_cli_run(
            version=VERSION,
            command=args.command,
            exit_code=1,
            duration_ms=duration_ms,
        )
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        maybe_report_cli_run(
            version=VERSION,
            command=args.command,
            exit_code=0,
            duration_ms=duration_ms,
        )


if __name__ == "__main__":
    main()
