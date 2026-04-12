"""
agent-trace CLI — terminal commands for AI code tracing.

Zero external dependencies — uses only the Python standard library.

Commands:
    agent-trace init              Initialize tracing for the current project
    agent-trace status            Show tracing status
    agent-trace reset             Reconfigure tracing settings
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
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import (
    DEFAULT_SERVICE_URL,
    get_auth_token,
    get_global_config,
    get_project_config,
    get_service_url,
    save_global_config,
    save_project_config,
)
from .blame import blame_file
from .commit_link import create_commit_link
from .context import context_command
from .registry import list_projects, lookup_or_create_project_id
from .trace import cli_resolve_project_root, discover_ambiguous_repo_roots, git_repo_root_for_path
from .hooks import (
    configure_claude_hooks,
    configure_cursor_hooks,
    configure_git_hooks,
    configure_git_notes_refspecs,
)
from .rules import add_rule, remove_rule, show_rules, list_available_rules, TOOL_CHOICES
from .record import record_from_stdin
from .rewrite import rewrite_ledgers
from .remote import (
    add_remote as remote_add,
    get_default_remote,
    list_remotes as remote_list,
    remove_remote as remote_remove,
    rename_remote as remote_rename,
    set_default_remote,
    set_remote_token,
    set_remote_url,
    show_remote as remote_show,
)
from .sync import pull as sync_pull, push as sync_push, status as sync_status

VERSION = "0.1.0"

VIEWER_BIN = os.path.expanduser("~/.agent-trace/bin/agent-trace-viewer")


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
    config = get_project_config()
    if config is not None:
        print("agent-trace is already initialized for this project.")
        print("Use 'agent-trace reset' to change configuration.")
        return

    print("Initializing agent-trace...\n")

    storage = _prompt("Storage mode", default="local", choices=["local", "remote"])
    project_config: dict = {"storage": storage}

    if storage == "remote":
        project_id = _prompt("Project ID")
        project_config["project_id"] = project_id
        project_config["service_url"] = DEFAULT_SERVICE_URL

        global_config = get_global_config()
        if global_config.get("auth_token"):
            print("Using global auth token (set via 'agent-trace set globaluser').")
        else:
            auth_token = _prompt("Auth Token")
            project_config["auth_token"] = auth_token

    save_project_config(project_config)
    from .storage import get_project_config_path, resolve_project_id
    pid = resolve_project_id(os.getcwd(), create=False)
    if pid:
        print(f"\nProject id: {pid}")
        print(f"Settings:   {get_project_config_path(pid)}")
        print(f"Pointer:    .agent-trace/project.json (checked in; ~200 bytes)")

    print()
    if _confirm("Configure hook for Cursor?", default=True):
        configure_cursor_hooks()
        print("  -> Cursor hooks configured (.cursor/hooks.json)")

    if _confirm("Configure hook for Claude Code?", default=True):
        configure_claude_hooks()
        print("  -> Claude Code hooks configured (.claude/settings.json)")

    if os.path.isdir(".git"):
        if _confirm("Configure git hooks? (post-commit + post-rewrite for attribution)", default=True):
            configure_git_hooks()
            print("  -> Git post-commit hook configured (.git/hooks/post-commit)")
            print("  -> Git post-rewrite hook configured (.git/hooks/post-rewrite)")
        if _confirm(
            "Add git notes refspecs for origin (fetch/push refs/notes/agent-trace)?",
            default=True,
        ):
            if configure_git_notes_refspecs():
                print("  -> Git notes refspecs configured for remote \"origin\"")
            else:
                print("  -> Skipped (no \"origin\" remote or not a git repo)")

    print("\nagent-trace initialized successfully!")


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
    print(f"  Storage:    local-first")

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
                if report.last_push:
                    print(f"    Last push: {report.last_push}")
                if report.last_pull:
                    print(f"    Last pull: {report.last_pull}")
                print()
                print("  Run 'agent-trace push' to share attributed work.")
                print("  Run 'agent-trace pull' to fetch teammates' changes.")
        except Exception:
            pass

    cursor_ok = os.path.exists(".cursor/hooks.json")
    claude_ok = os.path.exists(".claude/settings.json")
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
    print(f"\n  Cursor hook:       {'configured' if cursor_ok else 'not configured'}")
    print(f"  Claude Code hook:  {'configured' if claude_ok else 'not configured'}")
    print(f"  Git post-commit:   {'configured' if git_hook_ok else 'not configured'}")
    print(f"  Git post-rewrite:  {'configured' if git_rewrite_ok else 'not configured'}")


# ===================================================================
# reset
# ===================================================================

def cmd_reset(_args):
    config = get_project_config()
    if config is None:
        print("agent-trace is not set up for this project.")
        print("Run 'agent-trace init' to get started.")
        return

    print("Resetting agent-trace configuration...\n")

    storage = _prompt(
        "Storage mode",
        default=config.get("storage", "local"),
        choices=["local", "remote"],
    )
    new_config: dict = {"storage": storage}

    if storage == "remote":
        project_id = _prompt("Project ID", default=config.get("project_id", ""))
        new_config["project_id"] = project_id
        new_config["service_url"] = config.get("service_url", DEFAULT_SERVICE_URL)

        global_config = get_global_config()
        if global_config.get("auth_token"):
            print("Using global auth token (set via 'agent-trace set globaluser').")
        else:
            auth_token = _prompt("Auth Token", default=config.get("auth_token", ""))
            new_config["auth_token"] = auth_token

    save_project_config(new_config)
    print("\nConfiguration updated.")

    print()
    if _confirm("Reconfigure hook for Cursor?", default=False):
        configure_cursor_hooks()
        print("  -> Cursor hooks configured.")

    if _confirm("Reconfigure hook for Claude Code?", default=False):
        configure_claude_hooks()
        print("  -> Claude Code hooks configured.")


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
        show_unknown=getattr(args, "show_unknown", False),
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

    if rule_action == "add":
        tool = getattr(args, "tool", None)
        if not tool:
            print("--tool is required. Use --tool cursor or --tool claude", file=sys.stderr)
            sys.exit(1)
        path = add_rule(args.rule_name, tool)
        print(f"Rule '{args.rule_name}' added for {tool}: {path}")

    elif rule_action == "remove":
        tool = getattr(args, "tool", None)
        if not tool:
            print("--tool is required. Use --tool cursor or --tool claude", file=sys.stderr)
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
        print("Add a rule with: agent-trace rule add <name> --tool <cursor|claude>")

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
        try:
            entry = remote_add(
                pid, args.name, args.url,
                token=getattr(args, "token", None),
                token_env=getattr(args, "token_env", None),
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
        print(f"  Auth:     {info['auth_type']}")
        print(f"  Token:    {info['token_masked']}  (ref: {info['token_ref']})")

    elif action == "set-url":
        pid = _resolve_pid_for_remote()
        try:
            set_remote_url(pid, args.name, args.url)
            print(f"Remote '{args.name}' URL updated.")
        except ValueError as e:
            print(f"agent-trace remote set-url: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "set-token":
        pid = _resolve_pid_for_remote()
        try:
            set_remote_token(
                pid, args.name,
                token=getattr(args, "token", None),
                token_env=getattr(args, "token_env", None),
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
          f"{result.commit_links_pushed} commit-link(s)")
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
          f"{result.commit_links_pulled} commit-link(s)")
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
              f"{push_r.commit_links_pushed} commit-link(s)")
    if pull_r:
        print(f"Pulled {pull_r.traces_pulled} trace(s), "
              f"{pull_r.ledgers_pulled} ledger(s), "
              f"{pull_r.commit_links_pulled} commit-link(s)")


# ===================================================================
# notes (git refs/notes/agent-trace)
# ===================================================================


def _resolve_note_section_flags(args, cwd: str) -> tuple[bool, bool, bool]:
    """Merge ``--include-*`` / ``--no-include-*`` with project ``notes.*`` defaults."""
    from .git_notes import project_notes_flags

    d_l, d_s, d_p = project_notes_flags(cwd)

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
        sid = getattr(args, "session_id", None) or ""
        if not str(sid).strip():
            print("agent-trace summary generate: session_id required", file=sys.stderr)
            sys.exit(1)
        out = run_summary_generate(cwd, str(sid))
        if out is None:
            print("agent-trace summary generate: failed or no traces for session", file=sys.stderr)
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
        il, isum, ipr = _resolve_note_section_flags(args, cwd)
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
        note = build_note(
            led,
            traces,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
            summaries=summaries,
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
        il, isum, ipr = _resolve_note_section_flags(args, cwd)
        n = rebuild_notes_for_range(
            cwd,
            range_spec,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
        )
        print(f"Rebuilt notes for {n} commit(s)")
        return

    if action == "backfill":
        il, isum, ipr = _resolve_note_section_flags(args, cwd)
        since = getattr(args, "since", None)
        n = backfill_notes(
            cwd,
            since=since,
            include_ledger=il,
            include_summary=isum,
            include_prompts=ipr,
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
        if not sections:
            print("agent-trace notes strip: specify at least one of --ledger --summary --prompts", file=sys.stderr)
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
# Entry point
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="agent-trace",
        description="agent-trace — AI code tracing tool",
    )
    parser.add_argument(
        "--version", action="version", version=f"agent-trace {VERSION}",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("init", help="Initialize agent-trace for the current project")
    sub.add_parser("status", help="Show agent-trace status")
    sub.add_parser("reset", help="Reset agent-trace configuration")
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
        "--show-unknown",
        action="store_true",
        default=False,
        help="Include lines with no ledger (UNKNOWN); default is to omit them",
    )
    sub_blame.add_argument(
        "--require-attribution",
        action="store_true",
        default=False,
        help="Exit with non-zero status if any line is UNKNOWN (for CI)",
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

    # rule add <name> --tool <cursor|claude>
    rule_add = rule_sub.add_parser("add", help="Add a prebuilt rule")
    rule_add.add_argument("rule_name", help="Rule name (e.g. context-for-agents)")
    rule_add.add_argument("--tool", "-t", required=True, choices=TOOL_CHOICES,
                          help="Tool to add the rule for (cursor or claude)")

    # rule remove <name> --tool <cursor|claude>
    rule_rm = rule_sub.add_parser("remove", help="Remove a rule")
    rule_rm.add_argument("rule_name", help="Rule name (e.g. context-for-agents)")
    rule_rm.add_argument("--tool", "-t", required=True, choices=TOOL_CHOICES,
                         help="Tool to remove the rule from (cursor or claude)")

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
    r_add.add_argument("url", help="Remote URL")
    r_add.add_argument("--token", default=None, help="Auth token (stored globally)")
    r_add.add_argument("--token-env", default=None, help="Environment variable holding the token")

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

    ns_rebuild = notes_sub.add_parser("rebuild", help="Rebuild notes from local ledgers for a commit range")
    ns_rebuild.add_argument("range_spec", help="Range for git rev-list (e.g. HEAD~10..HEAD)")
    ns_rebuild.add_argument("--include-ledger", action="store_true")
    ns_rebuild.add_argument("--no-include-ledger", action="store_true")
    ns_rebuild.add_argument("--include-summary", action="store_true")
    ns_rebuild.add_argument("--no-include-summary", action="store_true")
    ns_rebuild.add_argument("--include-prompts", action="store_true")
    ns_rebuild.add_argument("--no-include-prompts", action="store_true")

    ns_backfill = notes_sub.add_parser("backfill", help="Rebuild notes for commits (optional --since)")
    ns_backfill.add_argument("--since", default=None, help="git rev-list --since (e.g. 2026-01-01)")
    ns_backfill.add_argument("--include-ledger", action="store_true")
    ns_backfill.add_argument("--no-include-ledger", action="store_true")
    ns_backfill.add_argument("--include-summary", action="store_true")
    ns_backfill.add_argument("--no-include-summary", action="store_true")
    ns_backfill.add_argument("--include-prompts", action="store_true")
    ns_backfill.add_argument("--no-include-prompts", action="store_true")

    ns_strip = notes_sub.add_parser("strip", help="Remove optional sections from a note")
    ns_strip.add_argument("commit", nargs="?", default="HEAD")
    ns_strip.add_argument("--ledger", dest="strip_ledger", action="store_true")
    ns_strip.add_argument("--summary", dest="strip_summary", action="store_true")
    ns_strip.add_argument("--prompts", dest="strip_prompts", action="store_true")

    ns_npush = notes_sub.add_parser("push", help="Push refs/notes/agent-trace to a remote")
    ns_npush.add_argument("--remote", default="origin")

    ns_npull = notes_sub.add_parser("pull", help="Fetch refs/notes/agent-trace from a remote")
    ns_npull.add_argument("--remote", default="origin")

    sub_summary = sub.add_parser("summary", help="Pluggable session summaries (command reads stdin JSON)")
    sum_sub = sub_summary.add_subparsers(dest="summary_action", metavar="ACTION", required=True)
    s_en = sum_sub.add_parser("enable", help="Enable and set the summary command")
    s_en.add_argument(
        "--command",
        dest="summary_command",
        required=True,
        help="Executable: JSON on stdin, JSON object on stdout",
    )
    s_en.add_argument(
        "--timeout",
        dest="summary_timeout",
        type=int,
        default=None,
        help="Timeout seconds (default 30)",
    )
    sum_sub.add_parser("disable", help="Disable session-end summaries")
    s_gen = sum_sub.add_parser("generate", help="Re-run summary for a session id")
    s_gen.add_argument("session_id", help="conversation_id / session_id")
    s_show = sum_sub.add_parser("show", help="Show merged file summaries for a commit")
    s_show.add_argument("commit", nargs="?", default="HEAD", help="Commit (default HEAD)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "reset": cmd_reset,
        "record": cmd_record,
        "commit-link": cmd_commit_link,
        "rewrite-ledger": cmd_rewrite_ledger,
        "viewer": cmd_viewer,
        "blame": cmd_blame,
        "context": cmd_context,
        "projects": cmd_projects,
        "adopt": cmd_adopt,
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


if __name__ == "__main__":
    main()
