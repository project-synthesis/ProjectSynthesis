# v0.4.13 Cycle 10 Rerun — Validation Result

**Status:** ❌ SHIP GATE STILL FAILS — 122 "database is locked" lines

**Date:** 2026-05-04
**Branch:** `release/v0.4.13` (70 commits ahead of main)
**Last fix:** `52d50022` cycle 9.5 — closed the most visible warm-path callers but missed deeper paths

## Lock breakdown (122 total)

| SQL | Count | Origin |
|---|---|---|
| `UPDATE optimizations` | 104 | pipeline_phases (cycle 4 supposedly migrated) + audit_logger |
| `INSERT INTO task_type_telemetry` | 19 | `task_type_classifier` writes — never migrated |
| `UPDATE prompt_cluster` | 15 | warm-path engine writes — bypassing queue |
| `INSERT INTO optimizations` | 12 | pipeline + bulk_persist paths |
| `INSERT INTO feedbacks` | 10 | feedback router — bypassing queue under contention |
| `UPDATE github_tokens` | 1 | github_auth (deferred per spec) |

## Source modules (top of lock origin)

| Module | Lock count |
|---|---|
| `app.services.taxonomy.engine` | 26 |
| `app.main` | 26 |
| `app.services.pipeline_phases` | 23 |
| `app.services.batch_persistence` | 14 |
| `app.providers.claude_cli` | 9 |
| `app.services.pipeline` | 8 |
| ... | ... |

## Health endpoint metrics during validation

```json
"write_queue": {
  "depth": 2,
  "total_submitted": 96,
  "total_completed": 80,
  "total_failed": 14,
  "p95_latency_ms": 131316,
  "p99_latency_ms": 131316,
  "max_observed_depth": 9,
  "worker_alive": true
}
```

- 14 of 96 queue submits FAILED (14.5% failure rate)
- p95 latency 131 seconds — writes blocked by 30s busy_timeout exhausting

## Root cause analysis

The cycle 7.5 polymorphic collapse changed function signatures to require `WriteQueue`, and the test fixtures installed a queue, but **production callers still create their own AsyncSession against the read engine** in many paths:

1. **Pipeline orchestrator** (`pipeline.py`) — passes the writer session through `persist_and_propagate` correctly, but earlier in the call chain (in `analyze` / `optimize` phases) opens its own session for SELECTs. If autoflush triggers a write from cached state, that write uses the read engine.

2. **Warm-path engine** (`taxonomy.engine`) — cycle 9.5 wired the recurring task at the top, but inner helpers (`_assign_cluster`, `_dissolve_node`, etc.) still call `await db.commit()` on whatever session was passed. If the caller passes a read-engine session, the commit lands there.

3. **task_type_telemetry** — `task_type_classifier.py` writes telemetry via `INSERT INTO task_type_telemetry` directly on the request's read session. Never migrated.

4. **batch_persistence** under contention — even though `bulk_persist` routes through `submit()`, the queue's writer connection still races with the read-engine sessions creating the contention.

The architectural gap: **two engines on the same SQLite file file means two writer slots competing**. The queue's `pool_size=1` only serializes within itself; it doesn't synchronize against any read-engine write.

## Per-criterion result

| # | Criterion | Result |
|---|---|---|
| 1 | 5 sequential probes | NOT RUN (aborted on lock detection) |
| 2 | N=3 concurrent probes | NOT RUN |
| 3 | Probe + warm | NOT RUN |
| 4 | REGRESSION BAR | RAN — captured 122 locks |
| 5 | 5 cancellation tests | NOT RUN |
| 6 | 5-min steady state | RAN — health metrics show 14 failures |
| 7 | Full backend test suite | NOT RUN this attempt (passes in unit tests because fixtures install queue) |
| 8 | Zero "database is locked" | **FAIL — 122** |

## Recommendation: v0.4.13 cannot ship

The infrastructure (cycles 1-9.5) built and wired the WriteQueue but the architectural assumption — that all writes route through it — is not actually true in production. The audit hook in WARN mode masked this for cycles 8-9. Cycle 10 live load surfaces it.

**Options:**

### Option A: v0.4.13.5 follow-up cycle (deep audit)

- Switch audit hook to RAISE mode in dev/staging
- Run probe + seed + feedback workload until first violation
- Trace each violation to its session creation
- Migrate every session-opening site to either:
  - Use the queue's writer session via `submit()`, OR
  - Mark the session explicitly `read_only=True` and audit-bypass

Estimated scope: 6-12 more callsite migrations, 2-4 day cycle.

### Option B: Rescope v0.4.13 to v0.4.14 (PostgreSQL migration)

The two-engine SQLite design is fighting the database's single-writer constraint. The spec § 10 already documented PostgreSQL as Option B. Skip to v0.4.14 with PostgreSQL where MVCC handles concurrent writers natively.

### Option C: Ship v0.4.13 with documented limitations

- Audit hook stays in WARN mode
- CHANGELOG documents that under sustained concurrent load (probe + seed + feedback simultaneously) some writes may still hit `database is locked` — fewer than v0.4.12 but not zero
- v0.4.14 P0 remains "complete the migration"

**Recommendation: Option A.** Option C ships partial value but doesn't honor the spec's OPERATE #8 invariant. Option B is too large a pivot at this stage.

## Files captured

- `backend.log` (845KB, 7142 lines)
- `health-final.json`
- `lock-breakdown.txt`
- `probe-runs-summary.txt` (empty — query failed during teardown)

End of cycle 10 rerun summary.
