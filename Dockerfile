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

# Generic error page — no application name, version, or tech stack hints
RUN mkdir -p /usr/share/nginx/html && \
    echo '<!DOCTYPE html><html><head><title>Service Unavailable</title></head><body><h1>Service temporarily unavailable</h1><p>Please try again later.</p></body></html>' > /usr/share/nginx/html/50x.html

EXPOSE 8080 8001
VOLUME ["/app/data"]

# Healthcheck hits the actual backend API, not just nginx serving the SPA
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -sf http://127.0.0.1:8080/api/health || exit 1

USER synthesis
WORKDIR /app/backend

ENTRYPOINT ["/app/docker-entrypoint.sh"]
