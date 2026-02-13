"""
agent-trace CLI — terminal commands for AI code tracing.

Zero external dependencies — uses only the Python standard library.

Commands:
    agent-trace init              Initialize tracing for the current project
    agent-trace status            Show tracing status
    agent-trace reset             Reconfigure tracing settings
    agent-trace record            Record a trace from stdin (used by hooks)
    agent-trace set globaluser    Set a global auth token
    agent-trace remove globaluser Remove the global auth token
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
from .hooks import configure_claude_hooks, configure_cursor_hooks
from .record import record_from_stdin

VERSION = "0.1.0"


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
    print("\nConfiguration saved to .agent-trace/config.json")

    print()
    if _confirm("Configure hook for Cursor?", default=True):
        configure_cursor_hooks()
        print("  -> Cursor hooks configured (.cursor/hooks.json)")

    if _confirm("Configure hook for Claude Code?", default=True):
        configure_claude_hooks()
        print("  -> Claude Code hooks configured (.claude/settings.json)")

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

    elif config.get("storage") == "local":
        traces_file = os.path.join(".agent-trace", "traces.jsonl")
        if os.path.exists(traces_file):
            with open(traces_file) as f:
                count = sum(1 for _ in f)
            print(f"  Traces:     {count} recorded")
        else:
            print("  Traces:     0 recorded")

    cursor_ok = os.path.exists(".cursor/hooks.json")
    claude_ok = os.path.exists(".claude/settings.json")
    print(f"\n  Cursor hook:      {'configured' if cursor_ok else 'not configured'}")
    print(f"  Claude Code hook: {'configured' if claude_ok else 'not configured'}")


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
# set globaluser
# ===================================================================

def cmd_set_globaluser(args):
    config = get_global_config()
    config["auth_token"] = args.token
    save_global_config(config)
    print("Global auth token saved to ~/.agent-trace/config.json")


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

    # set globaluser <token>
    set_p = sub.add_parser("set", help="Set global configuration")
    set_sub = set_p.add_subparsers(dest="set_command", metavar="KEY")
    gu = set_sub.add_parser("globaluser", help="Set global auth token")
    gu.add_argument("token", help="The auth token to store globally")

    # remove globaluser
    rm_p = sub.add_parser("remove", help="Remove global configuration")
    rm_sub = rm_p.add_subparsers(dest="remove_command", metavar="KEY")
    rm_sub.add_parser("globaluser", help="Remove global auth token")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "reset": cmd_reset,
        "record": cmd_record,
    }

    if args.command in dispatch:
        dispatch[args.command](args)
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
