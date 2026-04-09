#!/usr/bin/env bash
# Project Synthesis — VS Code bridge extension setup
# Detects VS Code installations, installs/updates the MCP Copilot Bridge
# extension, and validates the sampling pipeline integration.
#
# Usage: ./scripts/setup-vscode.sh [--code-path /path/to/code] [--build] [--uninstall] [--all]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXT_DIR="$PROJECT_DIR/VSGithub/mcp-copilot-extension"
EXT_ID="local.mcp-copilot-bridge"
VALIDATE_TIMEOUT=10  # seconds to wait for VS Code CLI responses

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    _RST='\033[0m' _GRN='\033[32m' _YLW='\033[33m' _RED='\033[31m'
    _CYN='\033[36m' _DIM='\033[90m' _BLD='\033[1m'
else
    _RST='' _GRN='' _YLW='' _RED='' _CYN='' _DIM='' _BLD=''
fi

_log()  { echo -e "${_CYN}[setup-vscode]${_RST} $*"; }
_ok()   { echo -e "  ${_GRN}✓${_RST} $*"; }
_fail() { echo -e "  ${_RED}✗${_RST} $*"; }
_warn() { echo -e "  ${_YLW}!${_RST} $*"; }
_info() { echo -e "  ${_DIM}$*${_RST}"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

CODE_PATH_OVERRIDE=""
DO_BUILD=false
DO_UNINSTALL=false
DO_ALL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --code-path)
            if [[ $# -lt 2 ]]; then
                _fail "--code-path requires a value"
                exit 1
            fi
            CODE_PATH_OVERRIDE="$2"; shift 2
            ;;
        --build)      DO_BUILD=true; shift ;;
        --uninstall)  DO_UNINSTALL=true; shift ;;
        --all)        DO_ALL=true; shift ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Install the MCP Copilot Bridge extension into VS Code."
            echo "Enables the sampling pipeline: Copilot's LLM runs the full"
            echo "optimization (analyze -> optimize -> score -> suggest) in your IDE."
            echo ""
            echo "Options:"
            echo "  --code-path PATH   Explicit path to 'code' binary"
            echo "  --build            Force rebuild .vsix from source before installing"
            echo "  --uninstall        Remove the extension instead of installing"
            echo "  --all              Install into ALL detected VS Code variants"
            echo "  -h, --help         Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                                # auto-detect and install"
            echo "  $0 --all                          # install into all VS Code variants"
            echo "  $0 --code-path /snap/bin/code     # target specific binary"
            echo "  $0 --build                        # rebuild from source first"
            echo "  $0 --uninstall                    # remove extension"
            exit 0
            ;;
        *)  _fail "Unknown option: $1"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# VS Code detection — multi-strategy search with deduplication
# ---------------------------------------------------------------------------

# Known binary names in priority order
VSCODE_BINS=(
    "code"              # Standard VS Code
    "code-insiders"     # VS Code Insiders
    "codium"            # VS Codium
    "code-oss"          # Code OSS (Arch, etc.)
    "code-exploration"  # VS Code Exploration
)

# Known installation paths beyond $PATH
VSCODE_SEARCH_PATHS=(
    # Linux standard
    "/usr/bin"
    "/usr/local/bin"
    "/usr/share/code/bin"
    "/usr/share/code-insiders/bin"
    # Snap (Ubuntu/Debian)
    "/snap/bin"
    # Flatpak exports
    "/var/lib/flatpak/exports/bin"
    "$HOME/.local/share/flatpak/exports/bin"
    # AppImage / custom
    "$HOME/Applications"
    "$HOME/.local/bin"
    # macOS
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin"
    "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin"
    "/Applications/VSCodium.app/Contents/Resources/app/bin"
    # Windows WSL interop
    "/mnt/c/Program Files/Microsoft VS Code/bin"
    "/mnt/c/Users/*/AppData/Local/Programs/Microsoft VS Code/bin"
)

# Flatpak app IDs
FLATPAK_IDS=(
    "com.visualstudio.code"
    "com.visualstudio.code.insiders"
    "com.vscodium.codium"
)

detect_all_vscode() {
    # Returns ALL found VS Code binaries (one per line), deduplicated by
    # resolved symlink target.  Handles snap symlinks, WSL interop paths,
    # and flatpak wrappers.
    local -A seen=()

    _add_if_new() {
        local path="$1"
        local resolved
        resolved=$(readlink -f "$path" 2>/dev/null || echo "$path")
        [[ -z "${seen[$resolved]:-}" ]] || return 1
        seen[$resolved]=1
        echo "$path"
    }

    # Strategy 1: User override
    if [[ -n "$CODE_PATH_OVERRIDE" ]]; then
        if [[ -x "$CODE_PATH_OVERRIDE" ]]; then
            echo "$CODE_PATH_OVERRIDE"
        elif [[ -e "$CODE_PATH_OVERRIDE" ]]; then
            _fail "Provided --code-path exists but is not executable: $CODE_PATH_OVERRIDE"
            _info "Check permissions: ls -la $(dirname "$CODE_PATH_OVERRIDE")"
        else
            _fail "Provided --code-path does not exist: $CODE_PATH_OVERRIDE"
        fi
        return 0  # User specified a path — don't auto-detect others
    fi

    # Strategy 2: PATH lookup
    for bin in "${VSCODE_BINS[@]}"; do
        local found
        found=$(command -v "$bin" 2>/dev/null) || continue
        _add_if_new "$found" || true
    done

    # Strategy 3: Known directories (expand globs for WSL)
    for search_dir in "${VSCODE_SEARCH_PATHS[@]}"; do
        for expanded_dir in $search_dir; do
            [[ -d "$expanded_dir" ]] || continue
            for bin in "${VSCODE_BINS[@]}"; do
                [[ -x "$expanded_dir/$bin" ]] || continue
                _add_if_new "$expanded_dir/$bin" || true
            done
        done
    done

    # Strategy 4: Flatpak
    if command -v flatpak &>/dev/null; then
        for app_id in "${FLATPAK_IDS[@]}"; do
            flatpak info "$app_id" &>/dev/null 2>&1 || continue
            local key="flatpak:$app_id"
            [[ -z "${seen[$key]:-}" ]] || continue
            seen[$key]=1
            echo "flatpak run $app_id"
        done
    fi

    # Strategy 5: Deep search in /opt (custom installs)
    if command -v find &>/dev/null; then
        while IFS= read -r found; do
            [[ -n "$found" ]] && _add_if_new "$found" || true
        done < <(find /opt -maxdepth 4 -name "code" -type f -executable 2>/dev/null)
    fi
}

# Wrapper to handle both direct paths and flatpak commands
run_code() {
    local code_cmd="$1"; shift
    if [[ "$code_cmd" == flatpak\ run\ * ]]; then
        timeout "$VALIDATE_TIMEOUT" bash -c "$code_cmd $(printf '%q ' "$@")" 2>/dev/null
    else
        timeout "$VALIDATE_TIMEOUT" "$code_cmd" "$@" 2>/dev/null
    fi
}

validate_binary() {
    # Verify the binary is functional, not just present.
    # Returns 0 if it can list extensions within VALIDATE_TIMEOUT seconds.
    local code_cmd="$1"
    run_code "$code_cmd" --version >/dev/null 2>&1
}

get_code_version() {
    run_code "$1" --version 2>/dev/null | head -1
}

get_code_label() {
    # Human-readable label: "code (snap)", "code-insiders", "codium (flatpak)"
    local code_cmd="$1"
    local base
    if [[ "$code_cmd" == flatpak\ run\ * ]]; then
        base="${code_cmd##*run }"
        echo "$base (flatpak)"
    elif [[ "$code_cmd" == /snap/* ]]; then
        base=$(basename "$code_cmd")
        echo "$base (snap)"
    else
        basename "$code_cmd"
    fi
}

# ---------------------------------------------------------------------------
# Extension management
# ---------------------------------------------------------------------------

get_installed_version() {
    local code_cmd="$1"
    local ext_line
    ext_line=$(run_code "$code_cmd" --list-extensions --show-versions 2>/dev/null \
               | grep -i "mcp-copilot-bridge" || true)
    if [[ -n "$ext_line" ]]; then
        echo "$ext_line" | sed 's/.*@//'
    fi
}

get_vsix_version() {
    if [[ -f "$EXT_DIR/package.json" ]]; then
        grep '"version"' "$EXT_DIR/package.json" | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/'
    fi
}

find_vsix() {
    ls -t "$EXT_DIR"/mcp-copilot-bridge-*.vsix 2>/dev/null | head -1
}

build_vsix() {
    _log "Building extension from source..."

    if ! command -v npm &>/dev/null; then
        _fail "npm not found — cannot build extension"
        _info "Install Node.js: https://nodejs.org/"
        return 1
    fi

    cd "$EXT_DIR" || { _fail "Extension directory missing: $EXT_DIR"; return 1; }

    # Install dependencies if needed
    if [[ ! -d "node_modules" ]]; then
        _info "Installing dependencies..."
        if ! npm install --no-audit --no-fund 2>&1 | tail -3; then
            _fail "npm install failed"
            cd "$PROJECT_DIR"
            return 1
        fi
    fi

    # Compile TypeScript
    _info "Compiling TypeScript..."
    if ! npm run compile 2>&1 | tail -1; then
        _fail "Compilation failed — check $EXT_DIR/src/extension.ts"
        cd "$PROJECT_DIR"
        return 1
    fi

    # Package .vsix — try global vsce first, fall back to npx
    if command -v vsce &>/dev/null; then
        _info "Packaging with vsce..."
        vsce package --no-dependencies 2>&1 | grep -vE "^$" | tail -3
    elif npx @vscode/vsce --version &>/dev/null 2>&1; then
        _info "Packaging with npx @vscode/vsce..."
        npx @vscode/vsce package --no-dependencies 2>&1 | grep -vE "^$" | tail -3
    else
        _fail "vsce not found — install with: npm install -g @vscode/vsce"
        cd "$PROJECT_DIR"
        return 1
    fi

    cd "$PROJECT_DIR"

    local vsix
    vsix=$(find_vsix)
    if [[ -z "$vsix" ]]; then
        _fail "Build completed but no .vsix file found"
        return 1
    fi

    _ok "Built: $(basename "$vsix")"
    return 0
}

install_extension() {
    local code_cmd="$1"
    local vsix="$2"
    local label
    label=$(get_code_label "$code_cmd")

    # Verify .vsix file is readable
    if [[ ! -r "$vsix" ]]; then
        _fail "Cannot read .vsix: $vsix"
        _info "Check permissions: ls -la $vsix"
        return 1
    fi

    _info "Installing $(basename "$vsix") into ${label}..."
    local output
    output=$(run_code "$code_cmd" --install-extension "$vsix" --force 2>&1)
    local rc=$?

    if [[ $rc -eq 0 ]]; then
        # Verify it actually installed
        local verify
        verify=$(get_installed_version "$code_cmd")
        if [[ -n "$verify" ]]; then
            _ok "Extension v${verify} installed in ${label}"
        else
            _ok "Install reported success for ${label}"
            _warn "Could not verify installation — restart VS Code to check"
        fi
        return 0
    elif [[ $rc -eq 124 ]]; then
        _fail "Installation timed out for ${label} (${VALIDATE_TIMEOUT}s)"
        _info "VS Code may be running in remote mode — try closing it first"
        return 1
    else
        _fail "Installation failed for ${label}: ${output%%$'\n'*}"
        # Provide actionable recovery
        if echo "$output" | grep -qi "permission"; then
            _info "Permission issue — try: sudo chown -R \$USER ~/.vscode/"
        elif echo "$output" | grep -qi "ENOENT\|not found"; then
            _info "Extension dir may be missing — try: mkdir -p ~/.vscode/extensions/"
        elif echo "$output" | grep -qi "EACCES"; then
            _info "Access denied — run VS Code once to initialize its directories"
        fi
        return 1
    fi
}

uninstall_extension() {
    local code_cmd="$1"
    local label
    label=$(get_code_label "$code_cmd")

    local installed
    installed=$(get_installed_version "$code_cmd")
    if [[ -z "$installed" ]]; then
        _info "Extension not installed in ${label} — nothing to remove"
        return 0
    fi

    _info "Removing mcp-copilot-bridge v$installed from ${label}..."
    local output
    output=$(run_code "$code_cmd" --uninstall-extension "$EXT_ID" 2>&1)
    local rc=$?

    if [[ $rc -eq 0 ]]; then
        _ok "Extension uninstalled from ${label}"
        return 0
    else
        _fail "Uninstall failed for ${label}: ${output%%$'\n'*}"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

validate_mcp_json() {
    local mcp_json="$PROJECT_DIR/.vscode/mcp.json"
    if [[ ! -f "$mcp_json" ]]; then
        _fail "Missing .vscode/mcp.json — native MCP discovery disabled"
        _info "This file should be in the repository. Try: git checkout .vscode/mcp.json"
        return 1
    fi

    if grep -q '"synthesis_mcp"' "$mcp_json" && grep -q '8001' "$mcp_json"; then
        _ok ".vscode/mcp.json present — native MCP discovery enabled"
        return 0
    else
        _warn ".vscode/mcp.json exists but may be misconfigured"
        _info "Expected: synthesis_mcp server on port 8001"
        return 1
    fi
}

validate_settings() {
    local settings="$PROJECT_DIR/.vscode/settings.json"
    if [[ ! -f "$settings" ]]; then
        _warn "No .vscode/settings.json — sampling pre-approval not configured"
        _info "Users will see a consent dialog on first sampling request"
        return 1
    fi

    if grep -q 'serverSampling' "$settings" && grep -q 'synthesis_mcp' "$settings"; then
        _ok "Sampling pre-approved in settings.json"
        return 0
    else
        _warn "settings.json exists but sampling not pre-approved"
        _info "Add chat.mcp.serverSampling to .vscode/settings.json"
        return 1
    fi
}

validate_server_reachable() {
    if ! command -v curl &>/dev/null; then
        _info "curl not available — skipping server reachability check"
        return 1
    fi

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
        "http://127.0.0.1:8001/mcp" -X POST \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup-check","version":"1.0.0"}}}' \
        2>/dev/null)
    if [[ "${http_code:0:1}" == "2" ]]; then
        _ok "MCP server reachable at :8001 (sampling endpoint healthy)"
        return 0
    elif [[ -n "$http_code" ]] && [[ "$http_code" != "000" ]]; then
        _warn "MCP server responded with HTTP $http_code"
        return 1
    fi

    _warn "MCP server not reachable — start with: ./init.sh start"
    return 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    echo ""
    _log "${_BLD}Project Synthesis — VS Code Integration Setup${_RST}"
    echo ""

    # ── Step 1: Detect VS Code installations ────────────────────────

    _log "Detecting VS Code..."
    local -a all_bins=()
    local line
    while IFS= read -r line; do
        [[ -n "$line" ]] && all_bins+=("$line")
    done < <(detect_all_vscode 2>/dev/null)

    if (( ${#all_bins[@]} == 0 )); then
        _fail "VS Code not found"
        echo ""
        _info "Searched: PATH, /usr/bin, /snap/bin, flatpak, /opt, ~/.local/bin"
        _info "Specify manually: $0 --code-path /path/to/code"
        echo ""
        echo -e "  ${_BLD}What you're missing:${_RST}"
        _info "The sampling pipeline lets Copilot's LLM run the full"
        _info "optimization (analyze -> optimize -> score -> suggest)"
        _info "directly in your IDE — no API key needed."
        echo ""
        exit 1
    fi

    # Validate each binary is functional, filter out broken ones
    local -a valid_bins=()
    local -a broken_bins=()
    for code_cmd in "${all_bins[@]}"; do
        if validate_binary "$code_cmd"; then
            valid_bins+=("$code_cmd")
        else
            broken_bins+=("$code_cmd")
        fi
    done

    if (( ${#valid_bins[@]} == 0 )); then
        _fail "Found VS Code but it is not responding"
        for b in "${broken_bins[@]}"; do
            _info "  Unresponsive: $b"
        done
        _info "VS Code may be running in a remote window or needs restart"
        _info "Try closing VS Code and running this script again"
        exit 1
    fi

    # Show all detected installations
    for code_cmd in "${valid_bins[@]}"; do
        local ver label
        ver=$(get_code_version "$code_cmd")
        label=$(get_code_label "$code_cmd")
        _ok "Found: ${_BLD}${label}${_RST} ${_DIM}v${ver} — ${code_cmd}${_RST}"
    done
    for b in "${broken_bins[@]}"; do
        _warn "Skipped (not responding): $b"
    done

    # Choose target(s)
    local -a targets=()
    if $DO_ALL || [[ -n "$CODE_PATH_OVERRIDE" ]]; then
        targets=("${valid_bins[@]}")
    elif (( ${#valid_bins[@]} > 1 )); then
        _info "Multiple installations found. Using first ($(get_code_label "${valid_bins[0]}"))"
        _info "Use --all to install into all, or --code-path for a specific one"
        targets=("${valid_bins[0]}")
    else
        targets=("${valid_bins[0]}")
    fi

    # ── Step 2: Handle uninstall ────────────────────────────────────

    if $DO_UNINSTALL; then
        local any_fail=false
        for code_cmd in "${targets[@]}"; do
            uninstall_extension "$code_cmd" || any_fail=true
        done
        $any_fail && exit 1
        exit 0
    fi

    # ── Step 3: Build if needed ─────────────────────────────────────

    echo ""
    local vsix
    vsix=$(find_vsix)

    if $DO_BUILD || [[ -z "$vsix" ]]; then
        build_vsix || exit 1
        vsix=$(find_vsix)
    fi

    if [[ -z "$vsix" ]]; then
        _fail "No .vsix file available"
        _info "Run with --build to compile from source"
        _info "Requires: npm, @vscode/vsce (npm install -g @vscode/vsce)"
        exit 1
    fi

    local vsix_version
    vsix_version=$(basename "$vsix" | sed 's/.*bridge-\(.*\)\.vsix/\1/')
    local source_version
    source_version=$(get_vsix_version)

    # ── Step 4: Install/update for each target ──────────────────────

    _log "Installing extension..."
    local any_installed=false
    local any_failed=false
    for code_cmd in "${targets[@]}"; do
        local installed
        installed=$(get_installed_version "$code_cmd")
        local label
        label=$(get_code_label "$code_cmd")

        if [[ "$installed" == "$vsix_version" ]] && ! $DO_BUILD; then
            _ok "Already at v${vsix_version} in ${label} — skipping"
            any_installed=true
            continue
        fi

        if [[ -n "$installed" ]]; then
            _info "Updating ${label}: v${installed} → v${vsix_version}"
        fi

        if install_extension "$code_cmd" "$vsix"; then
            any_installed=true
        else
            any_failed=true
        fi
    done

    # ── Step 5: Validate configuration ──────────────────────────────

    echo ""
    _log "Validating configuration..."
    validate_mcp_json
    validate_settings
    validate_server_reachable

    # ── Summary ─────────────────────────────────────────────────────

    echo ""
    if $any_failed; then
        _log "${_YLW}Setup completed with warnings${_RST}"
    else
        _log "${_GRN}Setup complete!${_RST}"
    fi
    echo ""
    echo -e "  ${_BLD}What's configured:${_RST}"
    echo -e "  ${_DIM}1. Bridge extension — Copilot gets Synthesis tools + sampling${_RST}"
    echo -e "  ${_DIM}2. .vscode/mcp.json — Native MCP discovery for VS Code 1.99+${_RST}"
    echo -e "  ${_DIM}3. Sampling pre-approved in settings.json${_RST}"
    echo ""
    echo -e "  ${_BLD}Next steps:${_RST}"
    echo -e "  ${_DIM}1. Start services:  ./init.sh start${_RST}"
    echo -e "  ${_DIM}2. Open this project in VS Code${_RST}"
    echo -e "  ${_DIM}3. Open Copilot Chat → click + → look for Synthesis tools${_RST}"
    echo ""
}

main
