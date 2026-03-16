# ============================================================
# Project Synthesis — Single-container production build
# Backend (FastAPI) + Frontend (SvelteKit static) + MCP + nginx
# ============================================================

# Stage 1: Build frontend static output
FROM node:24-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.14-slim AS runtime
WORKDIR /app

# Install nginx + curl (for healthcheck)
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r synthesis && useradd -r -g synthesis -d /app -s /bin/false synthesis

# Python deps — install CPU-only torch first (avoids 2GB+ CUDA download)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Then install remaining deps
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend code
COPY backend/app/ ./backend/app/
COPY backend/alembic/ ./backend/alembic/
COPY backend/alembic.ini ./backend/alembic.ini
# Prompt templates
COPY prompts/ ./prompts/

# Frontend static build
COPY --from=frontend-build /app/frontend/build /app/frontend/build

# nginx config
COPY nginx/nginx.conf /etc/nginx/nginx.conf

# Entrypoint
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

# Data directory (volume mount point)
RUN mkdir -p /app/data/traces /app/data/pids \
    && chown -R synthesis:synthesis /app/data

# nginx needs to write to /var/log/nginx and /run
RUN chown -R synthesis:synthesis /var/log/nginx /var/lib/nginx /run

# Custom error page for when backend is down
RUN mkdir -p /usr/share/nginx/html && \
    echo '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Service Unavailable</title><style>body{background:#06060c;color:#e4e4f0;font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}div{text-align:center}h1{color:#00e5ff;font-size:14px;letter-spacing:0.1em;text-transform:uppercase}p{color:#8b8ba8;font-size:12px}</style></head><body><div><h1>Project Synthesis</h1><p>Service temporarily unavailable. Please try again shortly.</p></div></body></html>' > /usr/share/nginx/html/50x.html

EXPOSE 8080 8001
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://127.0.0.1:8080/ || exit 1

USER synthesis

ENTRYPOINT ["/app/docker-entrypoint.sh"]
