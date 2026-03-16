#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"

mkdir -p "$DATA_DIR"

start_services() {
    echo "Starting services..."

    # Backend
    cd "$SCRIPT_DIR/backend"
    source .venv/bin/activate
    nohup python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000 --reload \
        > "$DATA_DIR/backend.log" 2>&1 &
    echo "  Backend started (PID $!, port 8000)"

    # MCP Server
    nohup python -m app.mcp_server \
        > "$DATA_DIR/mcp.log" 2>&1 &
    echo "  MCP server started (PID $!, port 8001)"
    deactivate

    # Frontend
    cd "$SCRIPT_DIR/frontend"
    nohup npm run dev -- --port 5199 --host 0.0.0.0 \
        > "$DATA_DIR/frontend.log" 2>&1 &
    echo "  Frontend started (PID $!, port 5199)"

    echo "All services started. Logs in data/*.log"
}

stop_services() {
    echo "Stopping services..."
    pkill -f "uvicorn app.main" 2>/dev/null && echo "  Backend stopped" || echo "  Backend not running"
    pkill -f "app.mcp_server" 2>/dev/null && echo "  MCP server stopped" || echo "  MCP server not running"
    pkill -f "vite.*5199" 2>/dev/null && echo "  Frontend stopped" || echo "  Frontend not running"
}

show_status() {
    echo "Service status:"
    pgrep -f "uvicorn app.main" > /dev/null 2>&1 && echo "  Backend:  running" || echo "  Backend:  stopped"
    pgrep -f "app.mcp_server" > /dev/null 2>&1 && echo "  MCP:      running" || echo "  MCP:      stopped"
    pgrep -f "vite.*5199" > /dev/null 2>&1 && echo "  Frontend: running" || echo "  Frontend: stopped"
}

case "${1:-start}" in
    start)   start_services ;;
    stop)    stop_services ;;
    restart) stop_services; sleep 2; start_services ;;
    status)  show_status ;;
    *)       echo "Usage: $0 {start|stop|restart|status}" ;;
esac
