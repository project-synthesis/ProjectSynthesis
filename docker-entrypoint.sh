#!/usr/bin/env bash
set -e

# ============================================================
# Project Synthesis — Container entrypoint
# Starts backend, MCP server, and nginx with graceful shutdown
# ============================================================

# Store PIDs for signal propagation
PIDS=()

cleanup() {
    echo "[entrypoint] Shutting down..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
    # Wait for processes to exit gracefully (max 10s)
    local waited=0
    while (( waited < 10 )); do
        local alive=0
        for pid in "${PIDS[@]}"; do
            kill -0 "$pid" 2>/dev/null && alive=1
        done
        (( alive == 0 )) && break
        sleep 1
        (( waited++ ))
    done
    echo "[entrypoint] Shutdown complete"
}

trap cleanup SIGTERM SIGINT SIGQUIT

echo "[entrypoint] Starting Project Synthesis..."

# Run Alembic migrations (WORKDIR is /app/backend)
echo "[entrypoint] Running database migrations..."
if ! python -m alembic upgrade head 2>&1; then
    echo "[entrypoint] ERROR: Database migration failed. Check alembic configuration."
    exit 1
fi
echo "[entrypoint] Migrations complete."

# Start backend (uvicorn)
echo "[entrypoint] Starting backend on :8000..."
python -m uvicorn app.main:asgi_app \
    --host 0.0.0.0 \
    --port 8000 \
    --log-level info \
    --no-access-log &
PIDS+=($!)

# Start MCP server
echo "[entrypoint] Starting MCP server on :8001..."
python -m app.mcp_server &
PIDS+=($!)

# Wait for backend to be ready before starting nginx
echo "[entrypoint] Waiting for backend..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        echo "[entrypoint] Backend ready"
        break
    fi
    if (( i == 30 )); then
        echo "[entrypoint] WARNING: Backend not responding after 30s — starting nginx anyway"
    fi
    sleep 1
done

# Start nginx (foreground-ish — we manage it via PID)
echo "[entrypoint] Starting nginx on :8080..."
nginx -g 'daemon off;' &
PIDS+=($!)

echo "[entrypoint] All services started. PIDs: ${PIDS[*]}"

# Wait for any process to exit
wait -n
EXIT_CODE=$?

echo "[entrypoint] Process exited with code $EXIT_CODE — shutting down all services"
cleanup
exit $EXIT_CODE
