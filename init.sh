#!/usr/bin/env bash
# Project Synthesis — service manager
# Usage: ./init.sh {start|stop|restart|status|logs}
set -uo pipefail
# Note: -e intentionally omitted — we handle errors per-command

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
PID_DIR="$DATA_DIR/pids"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
UVICORN="$BACKEND_DIR/.venv/bin/uvicorn"

BACKEND_PORT=8000
MCP_PORT=8001
FRONTEND_PORT=5199

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_log()  { echo "[init.sh] $*"; }
_ok()   { echo "  ✓ $*"; }
_fail() { echo "  ✗ $*"; }
_warn() { echo "  ! $*"; }

_ensure_dirs() {
    mkdir -p "$DATA_DIR" "$PID_DIR" "$DATA_DIR/traces"
}

_pid_file() {
    # $1 = service name (backend|mcp|frontend)
    echo "$PID_DIR/$1.pid"
}

_read_pid() {
    local pidfile
    pidfile="$(_pid_file "$1")"
    if [[ -f "$pidfile" ]]; then
        cat "$pidfile"
    fi
}

_is_running() {
    local pid
    pid="$(_read_pid "$1")"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

_wait_for_port() {
    # $1 = port, $2 = timeout seconds, $3 = label, $4 = optional health path
    local port="$1" timeout="$2" label="$3" health_path="${4:-}"
    local elapsed=0
    while (( elapsed < timeout )); do
        if [[ -n "$health_path" ]] && command -v curl &>/dev/null; then
            # Probe specific health endpoint
            curl -sf "http://127.0.0.1:${port}${health_path}" >/dev/null 2>&1 && return 0
        elif command -v nc &>/dev/null; then
            nc -z 127.0.0.1 "$port" 2>/dev/null && return 0
        else
            (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null && return 0
        fi
        sleep 1
        (( elapsed++ ))
    done
    return 1
}

_check_port_free() {
    # $1 = port, $2 = label
    if command -v lsof &>/dev/null; then
        if lsof -i :"$1" -sTCP:LISTEN >/dev/null 2>&1; then
            _fail "Port $1 already in use ($2). Run './init.sh stop' first."
            return 1
        fi
    fi
    return 0
}

_rotate_log() {
    # $1 = log file path — rotate if > 10MB
    local logfile="$1"
    if [[ -f "$logfile" ]]; then
        local size
        size=$(stat -c%s "$logfile" 2>/dev/null || stat -f%z "$logfile" 2>/dev/null || echo 0)
        if (( size > 10485760 )); then
            mv "$logfile" "${logfile}.1"
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
        _fail "Run: cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
        ok=false
    fi

    if [[ ! -x "$UVICORN" ]]; then
        _fail "uvicorn not found in venv"
        _fail "Run: cd backend && source .venv/bin/activate && pip install -r requirements.txt"
        ok=false
    fi

    if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
        _fail "Frontend node_modules not found"
        _fail "Run: cd frontend && npm install"
        ok=false
    fi

    if ! command -v node &>/dev/null; then
        _fail "Node.js not on PATH"
        ok=false
    fi

    if [[ "$ok" = false ]]; then
        _log "Preflight failed. Fix the above issues and retry."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

start_backend() {
    if _is_running backend; then
        _warn "Backend already running (PID $(_read_pid backend))"
        return 0
    fi
    _check_port_free "$BACKEND_PORT" "backend" || return 1
    _rotate_log "$DATA_DIR/backend.log"

    cd "$BACKEND_DIR"
    setsid nohup "$UVICORN" app.main:asgi_app \
        --host 127.0.0.1 \
        --port "$BACKEND_PORT" \
        --reload \
        >> "$DATA_DIR/backend.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$(_pid_file backend)"
    cd "$SCRIPT_DIR"

    if _wait_for_port "$BACKEND_PORT" 15 "backend" "/api/health"; then
        _ok "Backend ready (PID $pid, port $BACKEND_PORT)"
    else
        _warn "Backend started (PID $pid) but not responding yet — check data/backend.log"
    fi
}

start_mcp() {
    if _is_running mcp; then
        _warn "MCP already running (PID $(_read_pid mcp))"
        return 0
    fi
    _check_port_free "$MCP_PORT" "mcp" || return 1
    _rotate_log "$DATA_DIR/mcp.log"

    cd "$BACKEND_DIR"
    setsid nohup "$PYTHON" -m app.mcp_server \
        >> "$DATA_DIR/mcp.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$(_pid_file mcp)"
    cd "$SCRIPT_DIR"

    if _wait_for_port "$MCP_PORT" 10 "mcp"; then
        _ok "MCP server ready (PID $pid, port $MCP_PORT)"
    else
        _warn "MCP started (PID $pid) but not responding yet — check data/mcp.log"
    fi
}

start_frontend() {
    if _is_running frontend; then
        _warn "Frontend already running (PID $(_read_pid frontend))"
        return 0
    fi
    _check_port_free "$FRONTEND_PORT" "frontend" || return 1
    _rotate_log "$DATA_DIR/frontend.log"

    cd "$FRONTEND_DIR"
    setsid nohup npx vite dev --port "$FRONTEND_PORT" --host 127.0.0.1 \
        >> "$DATA_DIR/frontend.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$(_pid_file frontend)"
    cd "$SCRIPT_DIR"

    if _wait_for_port "$FRONTEND_PORT" 15 "frontend"; then
        _ok "Frontend ready (PID $pid, port $FRONTEND_PORT)"
    else
        _warn "Frontend started (PID $pid) but not responding yet — check data/frontend.log"
    fi
}

start_services() {
    preflight
    _ensure_dirs
    _log "Starting services..."
    start_backend
    start_mcp
    start_frontend
    echo ""
    _log "Logs: data/backend.log, data/frontend.log, data/mcp.log"
}

# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

stop_service() {
    # $1 = service name
    local name="$1"
    local pid
    pid="$(_read_pid "$name")"

    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        # PID file stale or missing — try pattern match as fallback
        case "$name" in
            backend)  pid=$(pgrep -f "uvicorn app.main" 2>/dev/null | head -1) ;;
            mcp)      pid=$(pgrep -f "app.mcp_server" 2>/dev/null | head -1) ;;
            frontend) pid=$(pgrep -f "vite.*${FRONTEND_PORT}" 2>/dev/null | head -1) ;;
        esac
    fi

    if [[ -z "$pid" ]]; then
        _warn "$name not running"
        rm -f "$(_pid_file "$name")"
        return 0
    fi

    # Graceful shutdown: SIGTERM the process group, wait up to 5s, then SIGKILL
    # Use negative PID to kill the entire process group (parent + children)
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
    local waited=0
    while (( waited < 5 )) && kill -0 "$pid" 2>/dev/null; do
        sleep 1
        (( waited++ ))
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill -9 -- -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
        _warn "$name force-killed (PID $pid)"
    else
        _ok "$name stopped (PID $pid)"
    fi

    rm -f "$(_pid_file "$name")"
}

stop_services() {
    _log "Stopping services..."
    stop_service frontend
    stop_service mcp
    stop_service backend
}

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

show_status() {
    _log "Service status:"
    for name in backend mcp frontend; do
        local pid port
        pid="$(_read_pid "$name")"
        case "$name" in
            backend)  port=$BACKEND_PORT ;;
            mcp)      port=$MCP_PORT ;;
            frontend) port=$FRONTEND_PORT ;;
        esac

        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "  $name: running (PID $pid, port $port)"
        else
            echo "  $name: stopped"
            # Clean stale PID file
            if [[ -n "$pid" ]]; then rm -f "$(_pid_file "$name")"; fi
        fi
    done
}

# ---------------------------------------------------------------------------
# Logs (tail all)
# ---------------------------------------------------------------------------

show_logs() {
    tail -f "$DATA_DIR/backend.log" "$DATA_DIR/frontend.log" "$DATA_DIR/mcp.log" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-start}" in
    start)   start_services ;;
    stop)    stop_services ;;
    restart) stop_services; sleep 2; start_services ;;
    status)  show_status ;;
    logs)    show_logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "  start    Start all services (backend, MCP, frontend)"
        echo "  stop     Gracefully stop all services"
        echo "  restart  Stop + start"
        echo "  status   Show running/stopped status with PIDs"
        echo "  logs     Tail all service logs"
        exit 1
        ;;
esac
