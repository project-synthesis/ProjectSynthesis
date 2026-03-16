# Phase 5: Deployment & Polish — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create init.sh service manager, Dockerfile + docker-compose, write CLAUDE.md/AGENTS.md, implement graceful shutdown, trace log rotation, and run a final integration test.

**Architecture:** init.sh manages 3 processes (backend, frontend, MCP). Single-container Docker with s6-overlay supervisor and nginx reverse proxy. Graceful shutdown marks in-flight optimizations as "interrupted". Daily trace rotation with configurable retention.

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `init.sh` | Service management (start/stop/restart/status) |
| `Dockerfile` | Single-container build (backend + frontend + MCP + nginx) |
| `docker-compose.yml` | Simplified compose config |
| `nginx/nginx.conf` | Reverse proxy config |
| `AGENTS.md` | Universal agent guidance + MCP passthrough protocol |
| `backend/tests/test_integration.py` | End-to-end integration test |

### Modify

| File | Changes |
|------|---------|
| `CLAUDE.md` | Rewrite with full operational guide for redesigned app |
| `backend/app/main.py` | Graceful shutdown handler |
| `backend/app/services/trace_logger.py` | Add rotation method |

---

## Chunk 1: Service Management + Graceful Shutdown

### Task 1: init.sh Service Manager

**Files:**
- Create: `init.sh`

The script manages 3 services: backend (uvicorn :8000), frontend (vite :5199), MCP server (:8001).

Commands: `start`, `stop`, `restart`, `status`.

Logs to `data/backend.log`, `data/frontend.log`, `data/mcp.log`.

- [ ] **Step 1: Write init.sh**
- [ ] **Step 2: Make executable and test**
- [ ] **Step 3: Commit**

---

### Task 2: Graceful Shutdown + Trace Rotation

**Files:**
- Modify: `backend/app/main.py` — add SIGTERM handler in lifespan
- Modify: `backend/app/services/trace_logger.py` — add `rotate()` method

Graceful shutdown: on SIGTERM, wait up to 30s for in-flight optimizations, mark remaining as "interrupted".

Trace rotation: `rotate(retention_days)` deletes JSONL files older than N days.

- [ ] **Step 1: Update main.py with shutdown handler**
- [ ] **Step 2: Add trace rotation to trace_logger.py**
- [ ] **Step 3: Run tests — no regressions**
- [ ] **Step 4: Commit**

---

## Chunk 2: Docker + Docs

### Task 3: Docker Configuration

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `nginx/nginx.conf`

Single container: Python backend + Node frontend + MCP + nginx.

- [ ] **Step 1: Write Dockerfile**
- [ ] **Step 2: Write docker-compose.yml**
- [ ] **Step 3: Write nginx.conf**
- [ ] **Step 4: Commit**

---

### Task 4: CLAUDE.md + AGENTS.md

**Files:**
- Modify: `CLAUDE.md` — full operational guide
- Create: `AGENTS.md` — MCP passthrough protocol

CLAUDE.md should cover: services/ports, backend layer rules, prompt template system, key env vars, common tasks (restart, test, verify MCP).

AGENTS.md should cover: quick start, MCP passthrough protocol (prepare → process → save), template editing, anti-patterns.

- [ ] **Step 1: Write CLAUDE.md**
- [ ] **Step 2: Write AGENTS.md**
- [ ] **Step 3: Commit**

---

## Chunk 3: Integration Test + Handoff

### Task 5: End-to-End Integration Test

**Files:**
- Create: `backend/tests/test_integration.py`

Test the full flow: optimize → refine → feedback → history. Uses mocked provider but exercises the real router → service → DB path.

- [ ] **Step 1: Write integration test**
- [ ] **Step 2: Run full suite with coverage**
- [ ] **Step 3: Commit**

---

### Task 6: Final Handoff

- [ ] **Step 1: Generate handoff-phase-5.json**
- [ ] **Step 2: Update orchestration protocol**
- [ ] **Step 3: Final commit**

---

## Exit Conditions Checklist

| # | Condition | Task |
|---|-----------|------|
| 1 | init.sh starts all 3 services | Task 1 |
| 2 | docker compose up --build works | Task 3 |
| 3 | CLAUDE.md written | Task 4 |
| 4 | AGENTS.md written | Task 4 |
| 5 | Graceful shutdown handles SIGTERM | Task 2 |
| 6 | Trace log rotation works | Task 2 |
| 7 | Final integration test passes | Task 5 |
| 8 | handoff-phase-5.json written | Task 6 |
