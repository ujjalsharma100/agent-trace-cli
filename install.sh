#!/usr/bin/env bash
set -euo pipefail

# =========================================================================
# agent-trace installer
#
# Usage (curl from GitHub):
#   curl -fsSL https://raw.githubusercontent.com/ujjalsharma100/agent-trace-cli/main/install.sh | bash
#
# Usage (local — from repo checkout):
#   ./install.sh
#
# What it does:
#   1. If run via curl (no source on disk), downloads repo from GitHub and re-runs
#   2. Checks for Python 3.9+
#   3. Copies the Python source to ~/.agent-trace/lib/
#   4. Creates an executable at ~/.agent-trace/bin/agent-trace
#   5. Adds ~/.agent-trace/bin to your PATH
#   6. After a curl install, deletes the temp download dir (archive + extracted tree)
# =========================================================================

INSTALL_DIR="${HOME}/.agent-trace"
GITHUB_REPO="https://github.com/ujjalsharma100/agent-trace-cli"
GITHUB_BRANCH="${AGENT_TRACE_INSTALL_BRANCH:-main}"
BIN_DIR="${INSTALL_DIR}/bin"
LIB_DIR="${INSTALL_DIR}/lib"

# -------------------------------------------------------------------
# Colours
# -------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}==>${NC} $1"; }
warn()  { echo -e "${YELLOW}Warning:${NC} $1"; }
error() { echo -e "${RED}Error:${NC} $1" >&2; exit 1; }

# -------------------------------------------------------------------
# 0.  Bootstrap: if run via curl (no source on disk), download from GitHub
# -------------------------------------------------------------------
bootstrap_if_remote() {
    if [ -n "${AGENT_TRACE_INSTALL_FROM_GITHUB:-}" ]; then
        return
    fi

    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || true
    if [ -f "${script_dir}/agent_trace/cli.py" ] || [ -f "${script_dir}/agent-trace-cli/agent_trace/cli.py" ]; then
        return
    fi

    info "No source tree found; downloading from GitHub ..."
    if ! command -v curl &>/dev/null; then
        error "curl is required to install from GitHub.  Install curl or clone the repo and run ./install.sh"
    fi

    local tmpdir tarball
    tmpdir="$(mktemp -d)"
    tarball="${tmpdir}/agent-trace-cli.tar.gz"

    if ! curl -fsSL "${GITHUB_REPO}/archive/refs/heads/${GITHUB_BRANCH}.tar.gz" -o "$tarball"; then
        error "Failed to download from GitHub.  Check your network or try again later."
    fi

    if ! tar xzf "$tarball" -C "$tmpdir"; then
        error "Failed to extract archive."
    fi

    local extract_dir="${tmpdir}/agent-trace-cli-${GITHUB_BRANCH}"
    if [ ! -f "${extract_dir}/install.sh" ]; then
        error "Unexpected archive layout.  Please clone the repo and run ./install.sh"
    fi

    export AGENT_TRACE_INSTALL_FROM_GITHUB=1
    # Entire tree (tarball + extracted repo) lives here; child removes this when done.
    export AGENT_TRACE_INSTALL_TMPDIR="$tmpdir"
    exec bash "${extract_dir}/install.sh"
}

# -------------------------------------------------------------------
# 1.  Check Python 3.9+
# -------------------------------------------------------------------
check_python() {
    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required but not found.  Install it first."
    fi

    local version
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major minor
    major="$(echo "$version" | cut -d. -f1)"
    minor="$(echo "$version" | cut -d. -f2)"

    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
        error "Python 3.9+ is required (found $version)."
    fi

    info "Found Python ${version}"
}

# -------------------------------------------------------------------
# 2.  Locate the Python source files
# -------------------------------------------------------------------
find_source() {
    # Resolve the directory this script lives in
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

    if [ -f "${SCRIPT_DIR}/agent_trace/cli.py" ]; then
        # Running from agent-trace-cli/ directory
        SOURCE_DIR="${SCRIPT_DIR}"
    elif [ -f "${SCRIPT_DIR}/agent-trace-cli/agent_trace/cli.py" ]; then
        # Running from repo root
        SOURCE_DIR="${SCRIPT_DIR}/agent-trace-cli"
    else
        error "Cannot find agent_trace source files.  Run this script from the repo directory."
    fi

    info "Source: ${SOURCE_DIR}"
}

# -------------------------------------------------------------------
# 3.  Install files
# -------------------------------------------------------------------
install_files() {
    info "Installing to ${INSTALL_DIR} ..."

    mkdir -p "${BIN_DIR}"
    mkdir -p "${LIB_DIR}/agent_trace"

    # Copy all Python modules (avoid a stale whitelist — new files must ship too)
    cp "${SOURCE_DIR}/agent_trace/"*.py "${LIB_DIR}/agent_trace/"

    if [ -d "${SOURCE_DIR}/agent_trace/schemas" ]; then
        mkdir -p "${LIB_DIR}/agent_trace/schemas"
        cp "${SOURCE_DIR}/agent_trace/schemas/"*.json "${LIB_DIR}/agent_trace/schemas/"
    fi

    # Create the executable entry-point
    cat > "${BIN_DIR}/agent-trace" << 'ENTRY_POINT'
#!/usr/bin/env python3
"""agent-trace CLI — entry point installed by install.sh."""
import os, sys
# Resolve symlinks so the lib dir is always found correctly
_here = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(_here), "lib"))
from agent_trace.cli import main
main()
ENTRY_POINT

    chmod +x "${BIN_DIR}/agent-trace"

    # Short alias: `at` → agent-trace
    ln -sf "${BIN_DIR}/agent-trace" "${BIN_DIR}/at"

    info "Installed ${BIN_DIR}/agent-trace (alias: at)"
}

# -------------------------------------------------------------------
# 4.  Install viewer
# -------------------------------------------------------------------
install_viewer() {
    local viewer_src="${SOURCE_DIR}/viewer"
    if [ ! -f "${viewer_src}/run_viewer.py" ]; then
        warn "Viewer source not found at ${viewer_src}; skipping viewer install."
        return
    fi

    local viewer_dir="${INSTALL_DIR}/viewer"
    info "Installing viewer to ${viewer_dir} ..."

    mkdir -p "${viewer_dir}"

    # Backend + launcher
    rm -rf "${viewer_dir}/backend"
    cp -r "${viewer_src}/backend" "${viewer_dir}/"
    cp "${viewer_src}/run_viewer.py" "${viewer_dir}/"

    # Frontend: optionally build, then copy
    if [ -f "${viewer_src}/frontend/package.json" ]; then
        if command -v npm &>/dev/null; then
            info "Building frontend ..."
            (cd "${viewer_src}/frontend" && npm install && npm run build) || warn "Frontend build failed; viewer will use pre-built dist if available."
        else
            warn "npm not found; using pre-built frontend dist if available."
        fi
    fi

    if [ -d "${viewer_src}/frontend" ]; then
        mkdir -p "${viewer_dir}/frontend"
        cp -r "${viewer_src}/frontend/src" "${viewer_dir}/frontend/" 2>/dev/null || true
        cp "${viewer_src}/frontend/index.html" "${viewer_dir}/frontend/" 2>/dev/null || true
        cp "${viewer_src}/frontend/package.json" "${viewer_dir}/frontend/" 2>/dev/null || true
        if [ -d "${viewer_src}/frontend/dist" ]; then
            cp -r "${viewer_src}/frontend/dist" "${viewer_dir}/frontend/"
        fi
    fi

    # Viewer launcher script
    cat > "${BIN_DIR}/agent-trace-viewer" << 'ENTRY_POINT'
#!/usr/bin/env python3
import os
import sys
VIEWER_DIR = os.path.expanduser(os.path.join("~", ".agent-trace", "viewer"))
os.chdir(VIEWER_DIR)
sys.path.insert(0, VIEWER_DIR)
from backend.main import main
main()
ENTRY_POINT

    chmod +x "${BIN_DIR}/agent-trace-viewer"
    info "Installed ${BIN_DIR}/agent-trace-viewer"
}

# -------------------------------------------------------------------
# 5.  Add to PATH
# -------------------------------------------------------------------
configure_path() {
    # Already on PATH?
    if echo "$PATH" | tr ':' '\n' | grep -qx "${BIN_DIR}"; then
        return
    fi

    local shell_name rc_file path_line
    shell_name="$(basename "${SHELL:-/bin/bash}")"

    case "$shell_name" in
        zsh)   rc_file="${HOME}/.zshrc" ;;
        bash)
            # Prefer .bash_profile on macOS, .bashrc on Linux
            if [ "$(uname)" = "Darwin" ]; then
                rc_file="${HOME}/.bash_profile"
            else
                rc_file="${HOME}/.bashrc"
            fi
            ;;
        fish)  rc_file="${HOME}/.config/fish/config.fish" ;;
        *)     rc_file="" ;;
    esac

    if [ -n "$rc_file" ]; then
        # Don't add twice
        if [ -f "$rc_file" ] && grep -q '.agent-trace/bin' "$rc_file" 2>/dev/null; then
            return
        fi

        {
            echo ""
            echo "# agent-trace"
            if [ "$shell_name" = "fish" ]; then
                echo "set -gx PATH \$HOME/.agent-trace/bin \$PATH"
            else
                echo 'export PATH="${HOME}/.agent-trace/bin:${PATH}"'
            fi
        } >> "$rc_file"

        info "Added ${BIN_DIR} to PATH in ${rc_file}"
    else
        warn "Could not detect your shell RC file.  Add this manually:"
        echo "  export PATH=\"\${HOME}/.agent-trace/bin:\${PATH}\""
    fi
}

# -------------------------------------------------------------------
# 6.  Offer global hook setup
# -------------------------------------------------------------------

# When the script is piped (e.g. curl ... | bash), stdin is not the terminal and
# plain `read` hits EOF immediately — exit status 1 triggers `set -e` and the
# installer aborts before "Installation complete". Read from /dev/tty instead.
read_y_n_prompt() {
    local prompt="$1"
    local reply=""
    if [ -t 0 ]; then
        read -rp "$prompt" reply || true
    elif [ -r /dev/tty ]; then
        read -rp "$prompt" reply < /dev/tty || true
    else
        warn "No TTY; skipping optional hook setup. Run: agent-trace hooks setup-global"
        printf '%s' 'n'
        return
    fi
    printf '%s' "$reply"
}

configure_global_hooks() {
    # Ensure agent-trace is on PATH for this function
    export PATH="${BIN_DIR}:${PATH}"

    if ! command -v agent-trace &>/dev/null; then
        warn "agent-trace not on PATH yet; skipping global hook setup."
        echo "  Run 'agent-trace hooks setup-global' after restarting your shell."
        return
    fi

    echo ""
    echo -e "  ${BOLD}Global hooks${NC}"
    echo "  Global hooks let agent-trace record traces in any initialised project,"
    echo "  no matter which directory the coding agent runs from."
    echo ""

    # Cursor
    if grep -q 'agent-trace record' "${HOME}/.cursor/hooks.json" 2>/dev/null; then
        info "Cursor global hooks already configured"
    else
        local answer
        answer=$(read_y_n_prompt "$(echo -e "${GREEN}==>${NC}") Set up global hooks for Cursor? [Y/n]: ")
        answer="${answer:-y}"
        if [[ "$answer" =~ ^[Yy] ]]; then
            agent-trace hooks setup-global --tool cursor
        fi
    fi

    # Claude Code
    if grep -q 'agent-trace record' "${HOME}/.claude/settings.json" 2>/dev/null; then
        info "Claude Code global hooks already configured"
    else
        local answer
        answer=$(read_y_n_prompt "$(echo -e "${GREEN}==>${NC}") Set up global hooks for Claude Code? [Y/n]: ")
        answer="${answer:-y}"
        if [[ "$answer" =~ ^[Yy] ]]; then
            agent-trace hooks setup-global --tool claude
        fi
    fi
}


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
main() {
    echo ""
    echo -e "  ${BOLD}agent-trace installer${NC}"
    echo "  ===================="
    echo ""

    bootstrap_if_remote
    check_python
    find_source
    install_files
    install_viewer
    configure_path
    configure_global_hooks

    echo ""
    info "Installation complete!"
    echo ""
    echo "  Restart your shell or run:"
    echo "    export PATH=\"\${HOME}/.agent-trace/bin:\${PATH}\""
    echo ""
    echo "  Then get started:"
    echo "    agent-trace --help"
    echo "    cd your-project && agent-trace init"
    echo ""

    # GitHub bootstrap: remove the whole mktemp dir (tarball + extracted clone), not only SOURCE_DIR.
    if [ -n "${AGENT_TRACE_INSTALL_FROM_GITHUB:-}" ]; then
        if [ -n "${AGENT_TRACE_INSTALL_TMPDIR:-}" ] && [ -d "${AGENT_TRACE_INSTALL_TMPDIR}" ]; then
            rm -rf "${AGENT_TRACE_INSTALL_TMPDIR}"
        elif [ -n "${SOURCE_DIR:-}" ]; then
            rm -rf "${SOURCE_DIR}"
        fi
    fi
}

main
