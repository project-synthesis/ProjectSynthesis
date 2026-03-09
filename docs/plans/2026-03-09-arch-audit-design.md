# Architecture Audit — Design Document

**Date:** 2026-03-09
**Scope:** Full-stack backend audit — database, API, memory, performance, security
**Approach:** Full overhaul (Approach 3)

---

## Problem Statement

A comprehensive audit of the backend identified 23 issues across six categories:
correctness bugs, memory leaks, performance bottlenecks, code duplication,
security/audit gaps, and missing schema infrastructure. This document captures
the approved design for addressing all of them.

---

## Section 1 — Database Layer

### Missing Indices

Five indices added to `optimizations` via `_migrate_add_missing_indexes()` in `database.py`:

| Column | Rationale |
|---|---|
| `status` | Filtered on every history query |
| `overall_score` | min/max score filter params |
| `primary_framework` | framework filter + sort column |
| `is_improvement` | stats aggregation filter |
| `linked_repo_full_name` | `has_repo` boolean filter |

Implementation: extend the existing `_migrate_add_missing_columns` pattern with a parallel
`_migrate_add_missing_indexes()` function that checks `sqlite_master` / `pg_indexes`
before issuing `CREATE INDEX IF NOT EXISTS`.

### SQLite WAL Mode & Pragma Tuning

Registered on the SQLAlchemy `connect` event:

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _):
    if "sqlite" in settings.DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")   # 64 MB page cache
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

WAL mode allows concurrent readers during writes — critical during long pipeline
runs that hold a write lock. `synchronous=NORMAL` is safe for WAL mode and
significantly faster than the default `FULL`.

### Engine Tuning

Add `pool_pre_ping=True` to `create_async_engine` to detect stale connections.
For PostgreSQL compatibility, add `pool_size=10`, `max_overflow=20`.

### Schema Additions

Two new nullable columns added via `_migrate_add_missing_columns`:

| Table | Column | Type | Purpose |
|---|---|---|---|
| `optimizations` | `deleted_at` | `DateTime` nullable | Soft-delete timestamp |
| `github_tokens` | `avatar_url` | `Text` nullable | Cached GitHub avatar URL |

`codebase_context_snapshot` is capped at **64 KB** at write time in `optimize.py`
before storing. A warning is logged when truncation occurs.

---

## Section 2 — Garbage Cleanup

### Background Cleanup Task (`services/cleanup.py`)

A new module started once in `lifespan()` as `asyncio.create_task()`, stored on
`app.state.cleanup_task`. Cancelled and awaited on shutdown.

Runs every **3600 seconds** (1 hour). Four independent sweeps per cycle:

| Table | Condition | Rationale |
|---|---|---|
| `refresh_tokens` | `expires_at < now` OR (`revoked = true` AND `created_at < now - 30d`) | Expired/revoked tokens accumulate forever |
| `github_tokens` | `expires_at < now - 24h` | 24h grace for clock skew |
| `linked_repos` | `linked_at < now - 30d` | Sessions have no TTL; repos accumulate |
| `optimizations` | `deleted_at IS NOT NULL AND deleted_at < now - 7d` | Purge soft-deleted rows after retention window |

Each sweep is wrapped in an independent `try/except` — one failure logs a warning
and skips that table; remaining sweeps continue. The task never crashes the process.

### In-Memory Repo Cache

`_repo_cache` in `github_repos.py` converted from an unbounded `dict` to a
size-bounded structure capped at **500 entries**. When the cap is reached, the
oldest entry (by insertion order, using `dict` FIFO semantics in Python 3.7+)
is evicted. TTL of 300s still enforced per-entry.

---

## Section 3 — Stats & Query Performance

### SQL Aggregates

`history.py:get_stats()` and `mcp_server.py:get_stats()` currently load every
`Optimization` row into Python to compute statistics. Replaced with a shared
`optimization_service.compute_stats(session, project)` function using SQL:

- `func.count()` and `func.avg(Optimization.overall_score)` — totals and average
- `GROUP BY task_type`, `GROUP BY primary_framework`, `GROUP BY provider_used` — breakdowns
- `func.count().filter(Optimization.is_improvement.is_(True))` — improvement rate
- `func.count().filter(Optimization.linked_repo_full_name.isnot(None))` — codebase-aware count

Result: one DB round-trip for all stats. At 10K records: ~100× less memory, ~10× faster.

### Code Deduplication

- `VALID_SORT_COLUMNS` defined once as a module-level constant in `optimization_service.py`,
  imported by `history.py` and `mcp_server.py` (eliminates three diverged copies).
- `compute_stats()` in `optimization_service.py` called by both `history.py` and
  `mcp_server.py` (eliminates duplicated aggregation logic).

### `secondary_frameworks` Encode Bug

In `update_optimization()`, the JSON-encode whitelist gains `"secondary_frameworks"`.
Currently callers passing a list get it stored as `"['a', 'b']"` (Python repr)
instead of valid JSON `'["a", "b"]'`. This silently breaks deserialization.

---

## Section 4 — Session & API Efficiency

### `github_me` Caching

`avatar_url` stored in `GitHubToken.avatar_url` (new column). Populated on
OAuth callback and on token refresh. `/auth/github/me` reads from DB — zero
external API calls. Invalidated on logout.

### OAuth Callback — Single httpx Client

Two sequential `async with httpx.AsyncClient()` blocks in `/auth/github/callback`
merged into one. Enables HTTP keep-alive connection reuse to the GitHub API host.

### PyGithub Construction

`_make_github(token: str) -> Github` helper extracted in `github_service.py`.
All functions (`get_user_repos`, `get_repo_tree`, `read_file_content`, etc.)
call this helper instead of inline-constructing `Github(auth=Auth.Token(token))`.
Centralises configuration; makes future per-request reuse straightforward.

### `optimize.py` Double-Session Removal

`optimize_prompt` currently takes `session: AsyncSession = Depends(get_session)`
but also opens a second `async_session()` inside `event_stream()` for the final
persist. The outer dependency is removed entirely. Both the initial "running"
record write and the final persist use `async_session()` inside `event_stream()`,
keeping all DB work within one closure and one session lifecycle.

---

## Section 5 — MCP Persistence & Audit Gap

### Problem

MCP `optimize` and `retry_optimization` tools run the full pipeline but save
nothing to the database. MCP-triggered optimizations are invisible in history,
absent from stats, and leave no audit trail.

### Fix

Both tools call `optimization_service.create_optimization()` before running the
pipeline (sets status `"pending"` → `"running"`). After each stage event, fields
are updated via `update_optimization()`. On completion, status is set to
`"completed"` or `"failed"`.

- `retry_optimization` sets `retry_of` on the new record.
- Tool return value is unchanged — callers still receive the same JSON.
- History UI and all MCP read tools reflect MCP-sourced runs automatically.

---

## Section 6 — Schema Additions & Remaining Fixes

### Soft-Delete

- `optimizations.deleted_at` added via migration.
- `DELETE /api/history/{id}` and MCP `delete_optimization` set `deleted_at = now()`.
- All `SELECT` queries add `.where(Optimization.deleted_at.is_(None))`.
- Background cleanup purges rows where `deleted_at < now - 7d`.
- New `GET /api/history/trash` lists soft-deleted records.

### `codebase_context_snapshot` Cap

Serialized JSON string capped at 65,536 chars before DB write. Warning logged
on truncation. Prevents multi-MB rows from large repo explorations.

### `avatar_url` on `GitHubToken`

Added via migration. Populated in OAuth callback and token refresh. Read by
`github_me` — eliminates live GitHub API call on every status check.

---

## Issue Inventory

| # | Category | Issue | File(s) |
|---|---|---|---|
| 1 | Performance | `get_stats` loads all rows into Python | `history.py`, `mcp_server.py` |
| 2 | Performance | Missing 5 DB indices | `database.py`, `models/optimization.py` |
| 3 | Memory leak | Expired `RefreshToken` rows never purged | `models/auth.py` |
| 4 | Memory leak | Expired `GitHubToken` rows never purged | `models/github.py` |
| 5 | Memory leak | Orphaned `LinkedRepo` rows never purged | `models/github.py` |
| 6 | Memory leak | `_repo_cache` grows without bound | `routers/github_repos.py` |
| 7 | Bug | `secondary_frameworks` not JSON-encoded in `update_optimization` | `services/optimization_service.py` |
| 8 | Bug | Double-session pattern in `optimize_prompt` | `routers/optimize.py` |
| 9 | Duplication | `_VALID_SORT_COLUMNS` defined in 3 files | `optimization_service.py`, `history.py`, `mcp_server.py` |
| 10 | Duplication | Stats logic duplicated in 2 files | `history.py`, `mcp_server.py` |
| 11 | Audit gap | MCP `optimize` doesn't persist to DB | `mcp_server.py` |
| 12 | Audit gap | MCP `retry_optimization` doesn't persist to DB | `mcp_server.py` |
| 13 | Performance | SQLite WAL mode not enabled | `database.py` |
| 14 | Performance | No `pool_pre_ping` on engine | `database.py` |
| 15 | Efficiency | Two httpx clients in OAuth callback | `routers/github_auth.py` |
| 16 | Efficiency | `github_me` makes live API call every request | `routers/github_auth.py` |
| 17 | Efficiency | PyGithub constructed inline in every function | `services/github_service.py` |
| 18 | Schema | No soft-delete on `optimizations` | `models/optimization.py` |
| 19 | Schema | `codebase_context_snapshot` has no size cap | `routers/optimize.py` |
| 20 | Schema | `avatar_url` not stored in `GitHubToken` | `models/github.py` |
| 21 | Schema | No `deleted_at` index for trash queries | `database.py` |
| 22 | Robustness | Cleanup task not running | `main.py` |
| 23 | Robustness | No `pool_pre_ping` for stale connection detection | `database.py` |

---

## Files Changed

| File | Change type |
|---|---|
| `backend/app/database.py` | WAL pragmas, index migration, engine tuning |
| `backend/app/models/optimization.py` | `deleted_at` column, `deleted_at` index |
| `backend/app/models/github.py` | `avatar_url` column on `GitHubToken` |
| `backend/app/services/cleanup.py` | **New** — background sweep task |
| `backend/app/services/optimization_service.py` | `compute_stats()`, `VALID_SORT_COLUMNS`, `secondary_frameworks` fix, soft-delete in `delete_optimization` |
| `backend/app/services/github_service.py` | `_make_github()` helper |
| `backend/app/routers/optimize.py` | Remove outer session dep, snapshot cap, soft-delete awareness |
| `backend/app/routers/history.py` | Import `VALID_SORT_COLUMNS`, call `compute_stats()`, soft-delete filter, trash endpoint |
| `backend/app/routers/github_auth.py` | Single httpx client, `avatar_url` persistence, `github_me` reads from DB |
| `backend/app/routers/github_repos.py` | Bounded `_repo_cache` |
| `backend/app/mcp_server.py` | Persist `optimize`/`retry_optimization`, import `VALID_SORT_COLUMNS`, call `compute_stats()` |
| `backend/app/main.py` | Start/stop cleanup task in lifespan |
