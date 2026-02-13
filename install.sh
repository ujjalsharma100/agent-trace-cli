#!/usr/bin/env bash
set -euo pipefail

# =========================================================================
# agent-trace installer
#
# Usage (hosted):
#   curl -fsSL https://your-domain.com/install.sh | bash
#
# Usage (local — from repo checkout):
#   ./agent-trace-cli/install.sh
#
# What it does:
#   1. Checks for Python 3.9+
#   2. Copies the Python source to ~/.agent-trace/lib/
#   3. Creates an executable at ~/.agent-trace/bin/agent-trace
#   4. Adds ~/.agent-trace/bin to your PATH
# =========================================================================

INSTALL_DIR="${HOME}/.agent-trace"
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

    # Copy Python modules
    for f in __init__.py cli.py config.py hooks.py record.py trace.py; do
        cp "${SOURCE_DIR}/agent_trace/${f}" "${LIB_DIR}/agent_trace/${f}"
    done

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

    # Copy .env.example → .env (only if .env doesn't exist yet)
    if [ ! -f "${INSTALL_DIR}/.env" ]; then
        if [ -f "${SOURCE_DIR}/.env.example" ]; then
            cp "${SOURCE_DIR}/.env.example" "${INSTALL_DIR}/.env"
            info "Created ${INSTALL_DIR}/.env (edit to configure)"
        fi
    else
        info "Keeping existing ${INSTALL_DIR}/.env"
    fi

    info "Installed ${BIN_DIR}/agent-trace"
}

# -------------------------------------------------------------------
# 4.  Add to PATH
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
# Main
# -------------------------------------------------------------------
main() {
    echo ""
    echo -e "  ${BOLD}agent-trace installer${NC}"
    echo "  ===================="
    echo ""

    check_python
    find_source
    install_files
    configure_path

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
}

main
