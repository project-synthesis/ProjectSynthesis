#!/usr/bin/env bash
# Project Synthesis — service manager
# Usage: ./init.sh {start|stop|restart|status|logs}
set -uo pipefail
# Note: -e intentionally omitted — we handle errors per-command

# ---------------------------------------------------------------------------
# Paths & ports
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
PID_DIR="$DATA_DIR/pids"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
UVICORN="$BACKEND_DIR/.venv/bin/uvicorn"
VITE="$FRONTEND_DIR/node_modules/.bin/vite"

BACKEND_PORT=8000
MCP_PORT=8001
FRONTEND_PORT=5199

# Graceful shutdown timeout per service (seconds).
# Backend needs 10s: uvicorn --reload supervisor → worker → async lifespan
# shutdown (routing stop + extraction tasks 5s wait_for + warm-path timer
# + strategy file watcher).
declare -A STOP_TIMEOUT=([backend]=10 [mcp]=5 [frontend]=5)

# Startup readiness timeout per service (seconds).
declare -A READY_TIMEOUT=([backend]=15 [mcp]=10 [frontend]=15)

# ---------------------------------------------------------------------------
# Output helpers (ANSI colors when stdout is a terminal)
# ---------------------------------------------------------------------------

if [[ -t 1 ]]; then
    _RST='\033[0m' _GRN='\033[32m' _YLW='\033[33m' _RED='\033[31m'
    _CYN='\033[36m' _DIM='\033[90m'
else
    _RST='' _GRN='' _YLW='' _RED='' _CYN='' _DIM=''
fi

_log()  { echo -e "${_CYN}[init.sh]${_RST} $*"; }
_ok()   { echo -e "  ${_GRN}✓${_RST} $*"; }
_fail() { echo -e "  ${_RED}✗${_RST} $*"; }
_warn() { echo -e "  ${_YLW}!${_RST} $*"; }

# ---------------------------------------------------------------------------
# Low-level utilities
# ---------------------------------------------------------------------------

_ensure_dirs() { mkdir -p "$DATA_DIR" "$PID_DIR" "$DATA_DIR/traces"; chmod 700 "$DATA_DIR"; }
_pid_file()    { echo "$PID_DIR/$1.pid"; }

_read_pid() {
    local f; f="$(_pid_file "$1")"
    [[ -f "$f" ]] && cat "$f"
}

_is_running() {
    local pid; pid="$(_read_pid "$1")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_svc_port() {
    case "$1" in
        backend)  echo "$BACKEND_PORT"  ;;
        mcp)      echo "$MCP_PORT"      ;;
        frontend) echo "$FRONTEND_PORT" ;;
    esac
}

_probe_port() {
    # $1 = port, $2 = optional HTTP health path (e.g. /api/health)
    # With health path: HTTP GET only — verifies the app is fully ready.
    # Without: TCP connect only — checks if anything listens on the port.
    local port="$1" health="${2:-}"
    if [[ -n "$health" ]]; then
        command -v curl &>/dev/null || return 1
        curl -sf --max-time 2 "http://127.0.0.1:${port}${health}" >/dev/null 2>&1
        return $?
    fi
    if command -v nc &>/dev/null; then
        nc -z 127.0.0.1 "$port" 2>/dev/null && return 0
    else
        (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null && return 0
    fi
    return 1
}

_check_port_free() {
    # $1 = port, $2 = label
    if _probe_port "$1"; then
        _fail "Port $1 in use ($2). Run './init.sh stop' first."
        return 1
    fi
    return 0
}

_wait_port_free() {
    # Block until port is released or timeout (seconds).
    local port="$1" timeout="$2" t0=$SECONDS
    while (( SECONDS - t0 < timeout )); do
        _probe_port "$port" || return 0
        sleep 0.5
    done
    return 1
}

MAX_LOG_FILES=5

_rotate_log() {
    local log_file="$1"
    local force="${2:-}"
    if [ -f "$log_file" ]; then
        local size
        size=$(stat -c%s "$log_file" 2>/dev/null || stat -f%z "$log_file" 2>/dev/null || echo 0)
        if [ "$size" -gt 10485760 ] || [ "$force" = "force" ]; then
            mv "$log_file" "${log_file}.$(date +%Y%m%d_%H%M%S)"
            # Prune old rotated logs beyond MAX_LOG_FILES
            local count
            count=$(ls -1 "${log_file}."* 2>/dev/null | wc -l)
            if [ "$count" -gt "$MAX_LOG_FILES" ]; then
                ls -1t "${log_file}."* 2>/dev/null | tail -n +$((MAX_LOG_FILES + 1)) | xargs rm -f 2>/dev/null
            fi
        fi
    fi
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

preflight() {
    local ok=true

    if [[ ! -x "$PYTHON" ]]; then
        _fail "Python venv not found at $PYTHON"
        echo -e "       ${_DIM}cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt${_RST}"
        ok=false
    fi

    if [[ ! -x "$UVICORN" ]]; then
        _fail "uvicorn not in venv"
        echo -e "       ${_DIM}cd backend && source .venv/bin/activate && pip install -r requirements.txt${_RST}"
        ok=false
    fi

    if [[ ! -x "$VITE" ]]; then
        _fail "Vite not found at $VITE"
        echo -e "       ${_DIM}cd frontend && npm install${_RST}"
        ok=false
    fi

    if ! command -v node &>/dev/null; then
        _fail "Node.js not on PATH"
        ok=false
    fi

    [[ "$ok" = false ]] && { _log "Preflight failed."; exit 1; }
}

# ---------------------------------------------------------------------------
# Launch (non-blocking — spawn process, write PID file, return immediately)
# ---------------------------------------------------------------------------
# Returns: 0 = launched, 1 = already running, 2 = port conflict

_launch() {
    local name="$1"
    if _is_running "$name"; then
        _warn "$name already running (PID $(_read_pid "$name"))"
        return 1
    fi
    local port; port="$(_svc_port "$name")"
    _check_port_free "$port" "$name" || return 2
    _rotate_log "$DATA_DIR/${name}.log" force

    case "$name" in
        backend)
            cd "$BACKEND_DIR"
            # --reload-dir: restrict file watching to app/ (skips .venv, data, tests)
            # --timeout-graceful-shutdown: cap connection drain wait.  With
            #   event_bus.shutdown() sending sentinels to SSE subscribers,
            #   connections close in ~0.1s.  The 3s ceiling is a safety net.
            setsid "$UVICORN" app.main:asgi_app \
                --host 127.0.0.1 --port "$BACKEND_PORT" \
                --reload --reload-dir app \
                --timeout-graceful-shutdown 3 \
                </dev/null >> "$DATA_DIR/backend.log" 2>&1 &
            ;;
        mcp)
            cd "$BACKEND_DIR"
            setsid "$PYTHON" -m app.mcp_server \
                </dev/null >> "$DATA_DIR/mcp.log" 2>&1 &
            ;;
        frontend)
            # Use vite binary directly — avoids npx → sh → node wrapper layers.
            cd "$FRONTEND_DIR"
            setsid "$VITE" dev --port "$FRONTEND_PORT" --host 127.0.0.1 \
                </dev/null >> "$DATA_DIR/frontend.log" 2>&1 &
            ;;
    esac
    echo "$!" > "$(_pid_file "$name")"
    cd "$SCRIPT_DIR"
    return 0
}

# ---------------------------------------------------------------------------
# Await readiness (parallel port polling for all launched services)
# ---------------------------------------------------------------------------

_await_ready() {
    local -a names=("$@")
    local -A ready health
    for n in "${names[@]}"; do
        ready[$n]=0
        health[$n]=""
    done
    health[backend]="/api/health"

    local t0=$SECONDS max_t=0
    for n in "${names[@]}"; do
        (( ${READY_TIMEOUT[$n]} > max_t )) && max_t=${READY_TIMEOUT[$n]}
    done

    while (( SECONDS - t0 <= max_t )); do
        local pending=false
        for n in "${names[@]}"; do
            (( ${ready[$n]} != 0 )) && continue
            local elapsed=$(( SECONDS - t0 ))
            local port; port="$(_svc_port "$n")"
            local pid; pid="$(_read_pid "$n")"

            # Detect process death during startup
            if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
                ready[$n]=2
                _fail "$n exited during startup — check data/${n}.log"
                continue
            fi

            if _probe_port "$port" "${health[$n]:-}"; then
                ready[$n]=1
                _ok "$n ready ${_DIM}(PID $pid, port $port)${_RST}"
            elif (( elapsed >= ${READY_TIMEOUT[$n]} )); then
                ready[$n]=2
                _warn "$n not responding after ${READY_TIMEOUT[$n]}s — check data/${n}.log"
            else
                pending=true
            fi
        done
        $pending || return 0
        sleep 1
    done
}

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

start_services() {
    preflight
    _ensure_dirs
    local t0=$SECONDS
    _log "Starting services..."

    # Launch all three processes concurrently (no cross-dependencies at startup).
    local -a to_await=()
    for svc in backend mcp frontend; do
        _launch "$svc"
        case $? in
            0) to_await+=("$svc") ;;   # freshly launched — wait for readiness
            1) ;;                       # already running (warning printed)
            2) exit 1 ;;               # port conflict — abort
        esac
    done

    if (( ${#to_await[@]} > 0 )); then
        _await_ready "${to_await[@]}"
    fi

    echo ""
    _log "Ready in $(( SECONDS - t0 ))s — logs: data/{backend,frontend,mcp}.log"
}

# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

stop_service() {
    local name="$1"
    local pid; pid="$(_read_pid "$name")"

    # Stale PID file — try pattern match as fallback
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        case "$name" in
            backend)  pid=$(pgrep -f -u "$(id -u)" "uvicorn app.main:asgi_app" 2>/dev/null | head -1) ;;
            mcp)      pid=$(pgrep -f -u "$(id -u)" "python.*app\.mcp_server" 2>/dev/null | head -1) ;;
            frontend) pid=$(pgrep -f -u "$(id -u)" "vite dev.*${FRONTEND_PORT}" 2>/dev/null | head -1) ;;
        esac
    fi

    if [[ -z "$pid" ]]; then
        echo -e "  ${_DIM}– $name already stopped${_RST}"
        rm -f "$(_pid_file "$name")"
        return 0
    fi

    local timeout=${STOP_TIMEOUT[$name]}

    # Phase 1: SIGTERM to the process only (not the process group).
    # For uvicorn --reload this lets the supervisor forward SIGTERM to the
    # worker, which runs the async lifespan shutdown (routing, extraction
    # tasks with 5s wait_for, warm-path timer, strategy file watcher).
    # Sending SIGTERM to the entire group simultaneously would bypass the
    # supervisor's orderly child shutdown and cause force-kills.
    kill "$pid" 2>/dev/null
    local t0=$SECONDS
    while (( SECONDS - t0 < timeout )) && kill -0 "$pid" 2>/dev/null; do
        sleep 0.5
    done

    # Phase 2: SIGKILL the entire process group if still alive.
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 -- -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
        sleep 0.2
        _warn "$name force-killed (PID $pid)"
    else
        _ok "$name stopped (PID $pid)"
    fi

    rm -f "$(_pid_file "$name")"
}

stop_services() {
    _log "Stopping services..."
    # Drain order: frontend (user traffic) → MCP (tool calls) → backend (data layer)
    stop_service frontend
    stop_service mcp
    stop_service backend
}

# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

do_restart() {
    stop_services

    # Verify ports are released (handles force-kill edge case where
    # the kernel hasn't fully cleaned up the socket yet).
    local need_wait=false
    for p in $BACKEND_PORT $MCP_PORT $FRONTEND_PORT; do
        _probe_port "$p" && { need_wait=true; break; }
    done
    if $need_wait; then
        echo -e "  ${_DIM}Waiting for ports to release...${_RST}"
        for p in $BACKEND_PORT $MCP_PORT $FRONTEND_PORT; do
            _wait_port_free "$p" 5 || { _fail "Port $p still in use"; exit 1; }
        done
    fi

    start_services
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

show_status() {
    _log "Service status:"
    for name in backend mcp frontend; do
        local pid port health_path=""
        pid="$(_read_pid "$name")"
        port="$(_svc_port "$name")"
        [[ "$name" = "backend" ]] && health_path="/api/health"

        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            if _probe_port "$port" "$health_path"; then
                echo -e "  ${_GRN}●${_RST} $name  ${_DIM}pid $pid  port $port${_RST}"
            else
                echo -e "  ${_YLW}●${_RST} $name  ${_DIM}pid $pid  port $port  (not responding)${_RST}"
            fi
        else
            echo -e "  ${_DIM}○ $name  stopped${_RST}"
            [[ -n "$pid" ]] && rm -f "$(_pid_file "$name")"
        fi
    done
    return 0
}

# ---------------------------------------------------------------------------
# Logs (tail all available)
# ---------------------------------------------------------------------------

show_logs() {
    local -a files=()
    for f in "$DATA_DIR"/{backend,frontend,mcp}.log; do
        [[ -f "$f" ]] && files+=("$f")
    done
    if (( ${#files[@]} == 0 )); then
        _warn "No log files found in $DATA_DIR/"
        return 1
    fi
    tail -f "${files[@]}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-start}" in
    start)   start_services ;;
    stop)    stop_services ;;
    restart) do_restart ;;
    status)  show_status ;;
    logs)    show_logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start    Start all services (backend, MCP, frontend)"
        echo "  stop     Graceful stop (SIGTERM → wait → SIGKILL)"
        echo "  restart  Stop then start"
        echo "  status   Show service health with PIDs"
        echo "  logs     Tail all service logs"
        exit 1
        ;;
esac
