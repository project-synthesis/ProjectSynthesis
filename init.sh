#!/usr/bin/env bash
# Project Synthesis — Development Environment Setup & Management
# Usage: ./init.sh [command]
# Commands: setup, start (default), stop, restart, status, seed, mcp, help

set -euo pipefail
cd "$(dirname "$0")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Ports (defaults — REDIS_PORT can be overridden via .env)
BACKEND_PORT=8000
FRONTEND_PORT=5199
MCP_PORT=8001
REDIS_PORT=6379

# Override REDIS_PORT from .env if present
if [ -f .env ]; then
    _env_redis_port=$(grep -E '^REDIS_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]"'"'" || true)
    if [ -n "$_env_redis_port" ]; then
        REDIS_PORT="$_env_redis_port"
    fi
fi

# PID files
PID_DIR=".pids"
mkdir -p "$PID_DIR" data

log() { echo -e "${CYAN}[Project Synthesis]${NC} $1"; }
ok() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; }

check_port() {
    local port=$1
    if command -v lsof &>/dev/null; then
        lsof -i ":$port" -sTCP:LISTEN &>/dev/null
    elif command -v ss &>/dev/null; then
        ss -tlnp | grep -q ":$port "
    elif command -v netstat &>/dev/null; then
        netstat -tlnp 2>/dev/null | grep -q ":$port "
    else
        curl -s -o /dev/null "http://localhost:$port" 2>/dev/null
    fi
}

check_redis() {
    if command -v redis-cli &>/dev/null; then
        redis-cli -p $REDIS_PORT ping &>/dev/null 2>&1
    else
        check_port $REDIS_PORT
    fi
}

wait_for_port() {
    local port=$1
    local name=$2
    local max_wait=${3:-30}
    local waited=0
    log "Waiting for $name on port $port..."
    while ! check_port "$port" && [ $waited -lt $max_wait ]; do
        sleep 1
        waited=$((waited + 1))
    done
    if check_port "$port"; then
        ok "$name is ready on port $port (${waited}s)"
        return 0
    else
        err "$name failed to start on port $port after ${max_wait}s"
        return 1
    fi
}

kill_by_pidfile() {
    local pidfile="$PID_DIR/$1.pid"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
            # I5: Validate PID belongs to expected service
            local cmd_match=false
            if [ -f "/proc/$pid/cmdline" ]; then
                local cmdline
                cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
                if echo "$cmdline" | grep -qiE '(uvicorn|node|npm|python|mcp)'; then
                    cmd_match=true
                fi
            elif command -v ps &>/dev/null; then
                local ps_cmd
                ps_cmd=$(ps -p "$pid" -o comm= 2>/dev/null || true)
                if echo "$ps_cmd" | grep -qiE '(uvicorn|node|npm|python|mcp)'; then
                    cmd_match=true
                fi
            else
                cmd_match=true  # Cannot validate — proceed anyway
            fi
            if [ "$cmd_match" = false ]; then
                warn "PID $pid does not match expected service — skipping (stale pidfile?)"
                rm -f "$pidfile"
                return 0
            fi
            # I1: Graceful shutdown — SIGTERM first, SIGKILL only survivors
            kill -15 "$pid" 2>/dev/null || true
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$pidfile"
    fi
}

kill_on_port() {
    local port=$1
    if command -v lsof &>/dev/null; then
        local pids
        pids=$(lsof -ti ":$port" 2>/dev/null || true)
        if [ -n "$pids" ]; then
            echo "$pids" | xargs kill -15 2>/dev/null || true
            sleep 2
            # SIGKILL only survivors
            for pid in $pids; do
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
            done
        fi
    elif command -v fuser &>/dev/null; then
        fuser -k "$port/tcp" 2>/dev/null || true
    fi
}

setup_backend() {
    log "Setting up Python backend..."
    cd backend

    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi

    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r requirements.txt 2>&1 | tail -1

    cd ..
    ok "Backend dependencies installed"
}

setup_frontend() {
    log "Setting up Node.js frontend..."
    cd frontend

    if [ ! -d "node_modules" ]; then
        npm install 2>&1 | tail -3
    fi

    cd ..
    ok "Frontend dependencies installed"
}

do_setup() {
    log "Installing all dependencies..."

    # Check prerequisites
    if ! command -v python3 &>/dev/null; then
        err "Python 3 is required but not found"
        exit 1
    fi
    if ! command -v node &>/dev/null; then
        err "Node.js is required but not found"
        exit 1
    fi

    setup_backend
    setup_frontend

    # Copy .env if not exists
    if [ ! -f .env ]; then
        cp .env.example .env 2>/dev/null || true
        warn "Created .env from .env.example — edit it with your API keys"
    fi

    # Redis hint
    if ! check_redis; then
        warn "Redis not detected — install and start Redis for persistent rate limiting and caching"
        warn "  Without Redis, the app works fine using in-memory fallbacks"
    fi

    ok "Setup complete!"
}

start_backend() {
    if check_port $BACKEND_PORT; then
        warn "Backend already running on port $BACKEND_PORT"
        return 0
    fi

    log "Starting backend on port $BACKEND_PORT..."
    cd backend
    source .venv/bin/activate

    # Export env vars from .env if it exists
    if [ -f ../.env ]; then
        set -a
        source ../.env 2>/dev/null || true
        set +a
    fi

    # Clear CLAUDECODE so the backend can launch its own Claude CLI sessions
    # (the backend runs independently and must not inherit nested-session protection)
    unset CLAUDECODE 2>/dev/null || true

    nohup python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port $BACKEND_PORT --reload > ../data/backend.log 2>&1 &
    echo $! > "../$PID_DIR/backend.pid"
    cd ..
}

start_frontend() {
    if check_port $FRONTEND_PORT; then
        warn "Frontend already running on port $FRONTEND_PORT"
        return 0
    fi

    log "Starting frontend on port $FRONTEND_PORT..."
    cd frontend
    nohup npm run dev > ../data/frontend.log 2>&1 &
    echo $! > "../$PID_DIR/frontend.pid"
    cd ..
}

start_mcp() {
    if check_port $MCP_PORT; then
        warn "MCP server already running on port $MCP_PORT"
        return 0
    fi

    log "Starting MCP server on port $MCP_PORT..."
    cd backend
    source .venv/bin/activate
    unset CLAUDECODE 2>/dev/null || true
    nohup python -m app.mcp_server > ../data/mcp.log 2>&1 &
    echo $! > "../$PID_DIR/mcp.pid"
    cd ..
}

do_start() {
    # Setup if not done yet
    if [ ! -d "backend/.venv" ] || [ ! -d "frontend/node_modules" ]; then
        do_setup
    fi

    # Check Redis (optional — warn only, don't attempt to start)
    if check_redis; then
        ok "Redis detected on port $REDIS_PORT"
    else
        warn "Redis not running on port $REDIS_PORT — rate limiting and caching will use in-memory fallback"
    fi

    start_backend
    wait_for_port $BACKEND_PORT "Backend" 30 || true

    start_frontend
    start_mcp

    wait_for_port $FRONTEND_PORT "Frontend" 30 || true

    echo ""
    log "═══════════════════════════════════════════"
    log "  Project Synthesis — Services Running"
    log "═══════════════════════════════════════════"
    ok "Backend:  http://localhost:$BACKEND_PORT"
    ok "Frontend: http://localhost:$FRONTEND_PORT"
    ok "MCP:      http://127.0.0.1:$MCP_PORT/mcp  (streamable-HTTP)"
    ok "MCP-WS:   ws://localhost:$BACKEND_PORT/mcp/ws  (WebSocket, backward-compat)"
    ok "API Docs: http://localhost:$BACKEND_PORT/api/docs"
    if check_redis; then
        ok "Redis:    localhost:$REDIS_PORT (connected)"
    else
        warn "Redis:    offline — using in-memory fallback"
    fi
    echo ""
    log "  Logs:"
    log "    Backend:  data/backend.log"
    log "    Frontend: data/frontend.log"
    log "    MCP:      data/mcp.log"
    log "═══════════════════════════════════════════"
}

do_stop() {
    log "Stopping all services..."

    kill_by_pidfile "backend"
    kill_by_pidfile "frontend"
    kill_by_pidfile "mcp"

    # Also kill by port as fallback
    kill_on_port $BACKEND_PORT
    kill_on_port $FRONTEND_PORT
    kill_on_port $MCP_PORT

    sleep 1
    ok "All services stopped"
}

do_restart() {
    do_stop
    sleep 1
    do_start
}

do_status() {
    echo ""
    log "Service Status:"

    if check_port $BACKEND_PORT; then
        ok "Backend:  RUNNING on port $BACKEND_PORT"
    else
        err "Backend:  STOPPED"
    fi

    if check_port $FRONTEND_PORT; then
        ok "Frontend: RUNNING on port $FRONTEND_PORT"
    else
        err "Frontend: STOPPED"
    fi

    if check_port $MCP_PORT; then
        ok "MCP:      RUNNING on port $MCP_PORT"
    else
        err "MCP:      STOPPED"
    fi

    if check_redis; then
        ok "Redis:    RUNNING on port $REDIS_PORT"
    else
        warn "Redis:    NOT RUNNING (optional — in-memory fallback active)"
    fi
    echo ""
}

do_seed() {
    log "Seeding example data..."
    cd backend
    source .venv/bin/activate
    python -m scripts.seed_examples
    cd ..
    ok "Seed complete"
}

do_help() {
    echo ""
    echo "Project Synthesis — Development Environment"
    echo ""
    echo "Usage: ./init.sh [command]"
    echo ""
    echo "Commands:"
    echo "  (none)    Install dependencies and start all services"
    echo "  setup     Install all dependencies only"
    echo "  start     Start all services (backend, frontend, MCP)"
    echo "  stop      Stop all running services"
    echo "  restart   Stop and restart all services"
    echo "  status    Show status of all services"
    echo "  seed      Populate database with example data"
    echo "  mcp       Start MCP server only"
    echo "  help      Show this help message"
    echo ""
    echo "Ports:"
    echo "  Backend:  $BACKEND_PORT"
    echo "  Frontend: $FRONTEND_PORT"
    echo "  MCP:      $MCP_PORT"
    echo "  Redis:    $REDIS_PORT (optional — in-memory fallback when unavailable)"
    echo ""
}

# Main dispatch
case "${1:-}" in
    setup)    do_setup ;;
    start)    do_start ;;
    stop)     do_stop ;;
    restart)  do_restart ;;
    status)   do_status ;;
    seed)     do_seed ;;
    mcp)
        if ! check_redis; then
            warn "Redis not running on port $REDIS_PORT — pipeline caching will use in-memory fallback"
        fi
        start_mcp; wait_for_port $MCP_PORT "MCP" 15
        ;;
    help)     do_help ;;
    "")       do_start ;;
    *)        err "Unknown command: $1"; do_help; exit 1 ;;
esac
