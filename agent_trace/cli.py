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
    agent-trace set globaluser    Set a global auth token
    agent-trace remove globaluser Remove the global auth token
    agent-trace projects          List registered project IDs (or: projects show <id>)
    agent-trace adopt [path]      Register a git repo and print its stable project_id
"""

from __future__ import annotations

import argparse
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
from .hooks import configure_claude_hooks, configure_cursor_hooks, configure_git_hooks
from .rules import add_rule, remove_rule, show_rules, list_available_rules, TOOL_CHOICES
from .record import record_from_stdin
from .rewrite import rewrite_ledgers

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

    print("agent-trace status\n")
    print(f"  Storage:    {config.get('storage', 'unknown')}")

    if config.get("storage") == "remote":
        print(f"  Project ID: {config.get('project_id', 'not set')}")
        print(f"  Service:    {get_service_url(config)}")

        token = get_auth_token(config)
        if token:
            masked = f"{'*' * 8}...{token[-4:]}"
            global_cfg = get_global_config()
            source = "global" if global_cfg.get("auth_token") == token else "project"
            print(f"  Auth Token: {masked}  (source: {source})")
        else:
            print("  Auth Token: not configured")

    from .storage import (
        get_commit_links_path,
        get_ledgers_path,
        get_project_dir,
        get_traces_path,
        resolve_project_id,
    )
    pid = resolve_project_id(os.getcwd(), create=False)

    if config.get("storage") == "local" and pid:
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

    p_projects = sub.add_parser("projects", help="List registered projects (or: projects show <id>)")
    p_projects.add_argument("projects_args", nargs="*", default=[], help="show <project_id>")

    p_adopt = sub.add_parser("adopt", help="Register a repo and print its project_id")
    p_adopt.add_argument(
        "adopt_path",
        nargs="?",
        default=".",
        help="Path to repository (default: current directory)",
    )

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
    }

    if args.command in dispatch:
        dispatch[args.command](args)
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


if __name__ == "__main__":
    main()
