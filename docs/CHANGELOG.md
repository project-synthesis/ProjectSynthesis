# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- **v0.4.16 P1a — Cold-path commit chunking with cumulative Q-gates** — replaces whole-refit atomicity with 4-phase SAVEPOINT-bounded execution (`cp_pre_reembed` outer + 4 inner per-phase nested SAVEPOINTs). Q-checks fire after Phase 1 (re-embed) + Phase 2 (reassign); Q regression rolls back to `cp_pre_reembed` (full revert preserves "Q never drops" invariant). New `ColdPathPhaseFailure` + `ColdPathQCheckEvalFailure` typed exceptions. Module-level `_COLD_PATH_LOCK` (asyncio.Lock) serializes concurrent invocations. New `_COLD_PATH_RUN_ID` ContextVar propagates correlation IDs through phase calls. `_restore_silhouette_from_snapshot()` defensively recovers in-memory state if process crashed mid-cold-path.
- **Peer-writer-aware quiescing** — `cluster_metadata["refit_in_progress_until"]` flag (timestamp-expiration is authoritative recovery primitive) lets peer writers (hot/warm/seed/feedback) cooperatively SKIP clusters mid-refit. 4 peer paths integrated: `engine.process_optimization` (hot path), `batch_persistence.bulk_persist`, `feedback_service.create_feedback`, seed (inherits via `bulk_persist`). Defensive `_parse_quiesce_flag()` handles 4 corruption paths (missing/non_string/iso_parse_fail/expired) with `flag_corrupt` decision events.
- **Cold-path observability** — 8 new decision-event types: `lock_acquired`, `cold_path_started`, `cold_path_phase_started`, `cold_path_phase_committed`, `cold_path_q_check`, `cold_path_phase_rolled_back`, `cold_path_completed`, `silhouette_restored_from_snapshot`. Plus per-batch `batch_progress` events at `COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL=10` threshold. `cold_path_q_check` event payload includes per-dimension breakdown (`q_coherence`, `q_separation`, `q_coverage`, `q_dbcv`, `q_stability`).
- **Health endpoint `cold_path` block** — exposes `last_run_at`, `last_run_duration_ms`, `last_run_q_delta`, `last_run_phases_committed`, `last_run_status`, `peer_skip_count_24h`, `rejection_count_24h`, `phase_failure_count_24h`, `p95_phase_duration_ms` per phase.
- **v0.4.16 P1b — Background indexing chunking + WriteQueue migration** — `_bg_index` + `RepoIndexService.build_index()` + `incremental_update()` + `invalidate_index()` migrated from legacy session to `WriteQueue.submit()`. Per-batch chunking at `REPO_INDEX_PERSIST_BATCH_SIZE=50` (DELETEs at `REPO_INDEX_DELETE_BATCH_SIZE=200`). Per-(repo, branch) `asyncio.Lock` registry serializes concurrent invocations; second call returns early via `repo_index_skipped(reason=lock_held)`. Lifespan startup sweep `_gc_orphan_repo_index_runs()` flips stuck `status='indexing'` rows older than `REPO_INDEX_LOCK_TTL_MIN=30 min` to `status='error'` so user can retry. Refit-fatal model: any batch failure marks meta error and re-raises; previously-committed batches stay in DB; idempotent retry via Phase 1 DELETE on rebuild. `_REPO_INDEX_RUN_ID` ContextVar correlates phase calls.
- **Repo-index observability** — 8 new decision-event types: `repo_index_lock_acquired`, `repo_index_started`, `repo_index_phase_started`, `repo_index_batch_committed`, `repo_index_batch_rolled_back`, `repo_index_skipped`, `repo_index_completed`, `repo_index_recovered`. Per-batch SSE `index_phase_changed` throttled at `REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL=5` batches. Reason-code enums (`_BATCH_ROLLBACK_REASONS`, `_SKIPPED_REASONS`, `_RECOVERED_REASONS`) enforced at emission via `_assert_reason_in_set`. 8 `TypedDict` payload schemas exported from `repo_index_service` `__all__` for downstream typed access.
- **Health endpoint `repo_index` block** — exposes `last_run_at`, `last_run_duration_ms`, `last_run_files_persisted`, `last_run_status`, `last_run_op`, `batches_committed_total`, `batches_rolled_back_total`, `p95_batch_duration_ms`, `p99_batch_duration_ms`, `active_locks` (10 fields).

### Changed
- **`is_cold_path_non_regressive()` signature** — added optional keyword-only `phase: int | None = None` kwarg (Cycle 2 wires phase context into Q-check events). Backward-compat: legacy callers omit phase.

### Configuration
- 7 new constants in `taxonomy/_constants.py`: `COLD_PATH_REEMBED_BATCH_SIZE=50`, `COLD_PATH_REASSIGN_BATCH_SIZE=200`, `COLD_PATH_LABEL_BATCH_SIZE=20`, `COLD_PATH_REPAIR_BATCH_SIZE=100`, `COLD_PATH_REFIT_QUIESCE_TIMEOUT_MIN=5`, `COLD_PATH_LOG_PROGRESS_BATCH_INTERVAL=10`, `COLD_PATH_LATENCY_RESERVOIR_SIZE=1000`. `_validate_cold_path_constants()` invariant assertion fires at module import.
- 6 new constants in `taxonomy/_constants.py` (P1b): `REPO_INDEX_PERSIST_BATCH_SIZE=50`, `REPO_INDEX_DELETE_BATCH_SIZE=200`, `REPO_INDEX_LOCK_TTL_MIN=30`, `REPO_INDEX_LOG_PROGRESS_BATCH_INTERVAL=5`, `REPO_INDEX_LATENCY_RESERVOIR_SIZE=1000`, `REPO_INDEX_LOCK_IDLE_EVICTION_SECONDS=3600`. `_validate_repo_index_constants()` invariant assertion fires at module import.

### Fixed
- **HistoryPanel pagination correctness (P0)** — backend now accepts `project_id` Query param on `GET /history` + `OptimizationService.list_optimizations()` accepts the matching kwarg. Frontend `getHistory()` + HistoryPanel call sites push `project_id: projectStore.currentProjectId` + `status: 'completed'` to the server, eliminating the ~9-row-per-page regression that surfaced post-ADR-005 multi-project rollout. New `$effect` re-fetches on project switch with `untrack()` race guard + capture-and-bail pattern at both fetch sites. Defensive client-side filters preserved as belt-and-suspenders.

## v0.4.14 — 2026-05-04

### Added
- `WriteQueue.submit_batch(work_fns)` helper for atomic multi-write groupings (single transaction, single session, single queue task). Used by OAuth callback + token revoke for atomic write+audit pairs.
- `SubmitBatchError` + `SubmitBatchCommitError` diagnostic exceptions with index/fn_name context. Lambda + functools.partial fn_name fallback covered by explicit pin test.

### Changed
- Migrated `tools/optimize.py:125` (passthrough pending insert) + `services/sampling_pipeline.py:846` (Optimization persist + applied-pattern tracking + injection-provenance flush) to write queue via `submit()`.
- Migrated 6 GitHub auth router write sites: `_refresh_token_if_expired`, `/auth/callback` (submit_batch), `/auth/me` cleanup-on-revoke + user-info-update, `/auth/logout`, `/auth/device/poll` revoke (submit_batch).
- Migrated 3 audit-log writers (strategies.py:164 strategy_updated, providers.py:124 api_key_set, providers.py:159 api_key_deleted) by threading `write_queue=get_write_queue()` into the existing `audit_logger.log_event()` kwarg (added in v0.4.13 cycle 8). Relaxed `log_event` signature to accept `db: AsyncSession | None = None` for kwarg-only callers.
- `routers/github_repos.py::_update_synthesis_status` accepts `write_queue` keyword; threaded through `_run_explore_synthesis` → background `_bg_index` task spawn sites. Catches `WriteQueueStoppedError` to survive lifespan teardown race during long-running explore synthesis.

### Fixed
- Audit-hook scope reduction holds: 0 read-engine audit warnings under v0.4.14 cycle 5 OPERATE regression bar (workload includes new MCP optimize-passthrough + sampling-pipeline + github_auth callback + strategy_updated + api_key flows).

## v0.4.13 — 2026-05-04

### Added

- **v0.4.13 P0 — `WriteQueue` single-writer queue worker** (`backend/app/services/write_queue.py`) — SQLite "database is locked" eliminated structurally rather than via per-call-site retry/mutex layering. All writes route through one async worker on a separate writer engine (`writer_engine` in `database.py`, `pool_size=1, max_overflow=0`); read paths use the original read engine and remain unaffected by writer serialization. `submit(work_fn, *, timeout=300, operation_label)` API returns the work function's result; cancellation of the caller does NOT cancel in-flight work (shielded `__aexit__`); per-task timeout DOES. Reentrancy hard-fails via `WriteQueueReentrancyError` if `submit()` is invoked from inside the worker task. Rolling reservoir produces p95/p99 submit-to-completion latency on the `/api/health` queue block. Hard regression bar from cycle 10: probe + 30 seeds + 100 feedbacks fully concurrent — 0 locks, 0 audit-hook warns. Spec: `docs/specs/sqlite-writer-queue-2026-05-02.md` (v6 APPROVED after 6 review rounds). Plan: `docs/superpowers/plans/2026-05-02-sqlite-writer-queue.md` (v3 APPROVED after 3 review rounds). Validation evidence: `docs/v0.4.13-validation/`.
- **v0.4.13 — Read-engine audit hook** (`backend/app/database.py::install_read_engine_audit_hook`) — fires on every read-engine `flush` that contains pending writes outside the allow-list flag set (`migration_mode` for lifespan ALTER TABLE migrations + `cold_path_mode` for taxonomy cold-path full-refit). Captures the offending stack frame and emits a structured WARNING in dev/prod (RAISE in CI via `WRITE_QUEUE_AUDIT_HOOK_RAISE`). Catches drift writes immediately at source instead of forcing forensic reconstruction from "database is locked" symptoms.
- **v0.4.13 — `/api/health` queue metrics** — new `write_queue` block exposes `depth`, `in_flight`, `total_submitted` / `total_completed` / `total_failed` / `total_timeout` / `total_overload`, `latency_p95_ms` / `latency_p99_ms`, `max_observed_depth`, `worker_alive`. Operators get queue saturation visibility without log scraping.
- **v0.4.13 — `app.dependencies.write_queue.get_write_queue` FastAPI dependency** + `register_process_write_queue` / `get_process_write_queue` module-level handle for non-FastAPI services (warm-path tasks, recurring GC, MCP process). `app.tools._shared.set_write_queue` / `get_write_queue` exposes the same handle to MCP tool handlers.

### Changed

- **v0.4.13 — All ~80 hot/warm writers migrated through the queue** — touch points: `bulk_persist`, `batch_taxonomy_assign`, `pipeline_phases.persist_and_propagate`, sampling persistence path, taxonomy warm-path 12 phase commits, hot-path engine 3 sites, snapshot writer, global pattern lifecycle (promote/validate/retire), `probe_service` 17 sites + read-only `self.db`, `feedback_service`, `optimization_service`, `audit_logger`, startup/recurring `gc`, `orphan_recovery`, REST routers (`optimize` / `domains` / `templates` / `github_repos` / `projects` / `clusters` / `feedback` / `history`), 3 of 7 MCP tool handlers (rest deferred to v0.4.14), and `task_type_telemetry` (cycle 9.6 fire-and-forget submit so telemetry failure never blocks the caller).
- **v0.4.13 — Lifespan ordering** — `main.py` lifespan now sequences: ALTER TABLE migrations → install audit hook → `write_queue.start()` → recurring tasks. Reverse on shutdown (cancel GC tasks BEFORE `write_queue.stop()` so queued GC writes drain cleanly). Ensures the audit hook is installed before any service-layer code can flush against the read engine.
- **v0.4.13 — Probe service `self.db` is now read-only** (cycle 7.5) — every probe write is funnelled through `submit()`. Closes the v0.4.12 probe persistence loss audit (`docs/audits/probe-v22-v29-2026-04-29.md`).
- **v0.4.13 — Polymorphic signatures collapsed to canonical `WriteQueue`** (cycle 7.5) — 4 functions previously accepted `WriteQueue | SessionFactory` (Option C dispatch during the migration). Migration completion lets the dual signature retire.
- **v0.4.13 — Writer engine separation** — `writer_engine` is its own SQLAlchemy engine with `pool_size=1, max_overflow=0`. Owned exclusively by the queue worker. The original read-side engine retains the production read pool so WAL read concurrency is preserved.
- **v0.4.13 — Cold path acknowledged exception** — taxonomy cold-path full-refit kept on `WriterLockedAsyncSession` + `cold_path_mode` audit-hook bypass. Refit's transaction span (multi-second commits across thousands of cluster rows) does not fit the queue's per-task timeout model. v0.4.15 chunks each refit phase into smaller `submit()` calls with `await asyncio.sleep(0)` between.

### Fixed

- **v0.4.13 P0 — SQLite "database is locked" lock storms** — root cause structurally eliminated. Within a single backend process, `WriterLockedAsyncSession` correctly serialized FLUSH calls via the process-wide asyncio.Lock, but SQLAlchemy's connection pool checked out separate underlying SQLite connections per session — the asyncio.Lock guarded the flush MOMENT, NOT the underlying connection's WAL writer-slot acquisition. When a connection released the asyncio.Lock after commit, it could still hold lingering WAL state during transition cleanup; the next writer's connection then saw `database is locked` despite holding the asyncio.Lock. The single-writer queue + dedicated `pool_size=1` writer engine collapses both layers into one ordering: every commit happens on the same connection, in monotonic order, with no possibility of WAL-slot races. Cycle 10 regression bar (probe + 30 seeds + 100 feedbacks fully concurrent) goes from **122 locks → 0 locks**, **54 audit warns → 0 warns**.
- **v0.4.13 — v0.4.12 probe persistence loss closed** — orphan audit on `probe_run` had found 11 of 26 historical runs in `status='running'` or silent-success state under writer contention. Verify-after-persist gate (v0.4.12) made failures loud; the queue migration removes the underlying race.
- **v0.4.13 — `task_type_telemetry` write contention** (cycle 9.6 Group B) — telemetry rows previously took the writer slot synchronously alongside hot-path inserts; bursts of classification fallbacks contended with `bulk_persist`. Now uses fire-and-forget `submit()` — telemetry failure does NOT block the caller.
- **v0.4.13 — Backend lifespan extraction listener bypassing queue** (cycle 9.6 Group E) — `_async_extract_listener` worker had a defensive `worker_alive` check missing on shutdown teardown; eliminated the last read-engine drift path during graceful stop.
- **v0.4.13 — Test fixture PRAGMA divergence** — the `writer_engine_file` test fixture now applies `journal_mode=WAL` + `busy_timeout=30000` + `synchronous=NORMAL` + `cache_size=-64000` + `foreign_keys=ON` identically to production. Closes the test-vs-prod PRAGMA gap that previously masked production-only locking modes during development.
- **v0.4.13 — `WriterLockedAsyncSession` retained as defense-in-depth** — kept until v0.4.14 audit hook switches to RAISE in production (currently WARN dev/prod, RAISE only in CI). Belt-and-suspenders during the 7-day post-release watch window.

### Removed

- **v0.4.13 — `_persist_lock` module-level asyncio.Lock from `bulk_persist`** — queue serializes by construction, no per-callsite lock needed.
- **v0.4.13 — `_MAX_PERSIST_ATTEMPTS=5` + `_PERSIST_BACKOFF_SECS=5.0` retry loop** — retries existed because lock storms were the dominant failure mode; with the queue, no longer needed.
- **v0.4.13 — `usage_db` separate-session pattern from `pipeline_phases.persist_and_propagate`** — queue callback subsumes it; a single `submit()` writes both the optimization row and the usage counters in one ordered transaction.
- **v0.4.13 — `meta_db` separate session from warm-path split-rejection metadata** — replaced by the canonical savepoint+autobegin pattern inside the queue's worker session.
- **v0.4.13 — Probe service `_persist_lock`** (cycle 7.5) — queue serialization replaces the per-instance lock.
- **v0.4.13 — Polymorphic `WriteQueue | SessionFactory` Option C dispatch** in 4 functions (cycle 7.5 collapse to canonical) — migration substrate retired now that 100% of hot+warm writers are queue-routed.

## v0.4.12 — 2026-05-02

### Added

- **v0.4.12 — Probe Phase 3 wired to canonical `batch_pipeline`** — fulfils the Tier 1 spec's "peer of seed agents — same execution primitive" mandate. Pre-fix, probe ran a hand-rolled per-prompt loop that called `enrich()` and used only `.analysis`, discarding `codebase_context`, `applied_patterns`, `divergence_alerts`, `enrichment_meta`. Persisted rows were structurally hollow — NULL `optimized_prompt`, identical 6.80 stub scores across every prompt, no `OptimizationPattern(relationship='injected')` rows. Now delegates to `run_batch + bulk_persist + batch_taxonomy_assign` exactly as the seed path does. Probe rows are first-class Optimization rows with full hybrid LLM-blended scoring, multi-embedding, normalized intent_label, canonicalized domain via DomainResolver, cluster assignment + post-commit pattern provenance.

- **v0.4.12 — Rate-limit handling end-to-end** — provider-layer typed errors + passthrough fallback + UI surface. `ProviderRateLimitError` now carries `reset_at: datetime` + `provider_name` + unified `estimated_wait_seconds` property. `claude_cli.py` parses MAX-style "resets 3:40pm (America/Toronto)" messages into UTC datetimes (plan-agnostic — falls through cleanly for any plan that doesn't use this format). When a prompt hits 429 past retries, `batch_pipeline._build_passthrough_fallback_pending()` returns a graceful heuristic-only row (`scoring_mode='heuristic'`, `routing_tier='passthrough_fallback'`) so the user gets a usable degraded result instead of a failure. Frontend surfaces:
  - New `rate_limit_active` / `rate_limit_cleared` SSE events
  - `rateLimitStore` (per-second tick, multi-provider tracking)
  - `RateLimitBanner.svelte` global banner (live countdown + "running in passthrough mode" copy)
  - Settings panel "Rate limits" accordion (auto-opens on hit, shows reset_at + behavior explanation)
  - One-shot info toast on first hit per provider
  - Plan-agnostic provider labels everywhere ("Claude CLI", not "Claude MAX (CLI)") since the CLI works against any Anthropic plan (Pro/Team/Enterprise/MAX/Bedrock/Vertex).

- **v0.4.12 — Pattern-injection provenance on probe + seed rows (task #97)** — `batch_pipeline` now captures `auto_inject_patterns` output on `PendingOptimization` and `bulk_persist` writes `OptimizationPattern(relationship='injected')` rows POST-commit (FK-on-Optimization fires otherwise). Pre-fix, the SAVEPOINT inside `auto_inject_patterns` silently rolled back on every probe/seed row → zero injected provenance even when patterns were used during generation. Probe and seed rows now record provenance identical to the canonical pipeline.py path, populating the Inspector's "applied patterns" section.

### Fixed

- **v0.4.12 — Probe persistence: verify-after-persist gate (no silent success)** — historical behavior swallowed `bulk_persist` exceptions and continued; the probe then computed `mean_overall` from in-memory PendingOptimization objects (whose `status='completed'` was set during scoring) and reported `status='completed'` on the ProbeRun row even when ZERO Optimization rows actually landed in the DB. Outright correctness defect, not a UX gap. Orphan audit on `probe_run` found 11 of 26 historical runs in this exact state — every probe since the canonical-batch refactor at v9 lost its rows under any level of writer contention. Now the probe queries the DB for the canonical truth of what got persisted before reporting anything to the user. Three outcomes: full (proceed normally), partial (drop the ghost rows from `prompt_results` so aggregate + taxonomy_delta only count what's durable), catastrophic (raise so the top-level except handler marks the row failed with structured error info preserving the underlying persistence cause).

- **v0.4.12 — Probe persistence: per-prompt streaming** — pre-fix the probe ran `run_batch` to completion, then called `bulk_persist(all_5_pendings)` as ONE 5-row transaction at the end. That batched commit was the largest single SQLite write in the system and the most likely to lose the WAL writer-slot race against concurrent backend warm-path maintenance. Now `_on_progress` spawns one `asyncio.create_task(_persist_one(p))` per completion, so each row lands in its own single-INSERT transaction (~tens of ms hold time on the WAL slot, vs hundreds of ms for the 5-row variant). Bonus side-effect: `bulk_persist` already publishes one `optimization_created` event-bus event per inserted row, and the frontend's `+page.svelte` already routes those through `HistoryPanel` for surgical row insertion — so per-prompt persist gives real-time UI updates as each prompt finishes scoring instead of all-at-once at end-of-batch.

- **v0.4.12 — Probe persistence: early-abort on catastrophic** — when the first per-prompt persist exhausts its retry budget (5 attempts × exponential backoff = 75s), writer-slot contention is sustained → remaining peer prompts' persist tasks will also fail. Pre-fix all 5 LLM pipelines ran to completion regardless — worst-case 12-20 minutes of Opus 4.7 audit-class calls wasted on a probe that the verify-gate would mark catastrophic anyway. Now `_persist_one`'s except block sets an `abort_event`; an `_abort_watcher` coroutine cancels the `run_batch` task, which propagates `CancelledError` through `asyncio.gather` to every in-flight LLM call. Already-spawned persist tasks continue (idempotent + retried). Wall-time savings: catastrophic failures now resolve within ~75s of the first persist trip-wire instead of the full LLM-pipeline window.

- **v0.4.12 — Warm-path Groundhog Day loop fix** — `_warm_path_age` only incremented in `phase_audit()` at the END of a successful cycle. If a cycle failed early (e.g., Phase 0's autoflush hit "database is locked", session ended in PendingRollbackError), age stayed at 0, which kept `is_first_warm_cycle()` returning True, which forced every subsequent cycle to re-run the full `dirty_ids=None` scan that triggered the same failure. Visible as `Warm path cycle: dirty_ids=all (first_cycle=True, ...)` → `Warm path failed: PendingRollbackError` repeating every cadence interval. Now increment age in the except block so the next cycle uses `snapshot_dirty_set_with_projects` (which empties the dirty_set) and skips when nothing changed — natural recovery path. Confirmed: 1 warm-path cycle in 2 minutes after restart vs 9 in 4 minutes before the fix.

- **v0.4.12 — `WriterLockedAsyncSession` — process-wide writer mutex** — eliminates SQLite "database is locked" lock storms architecturally rather than via per-call-site mutex wrapping. Subclasses `AsyncSession` to auto-acquire `db_writer_lock` on first `flush()` (gated on `self.new or self.dirty or self.deleted` so read-only sessions DON'T acquire) and release on `commit()`/`rollback()`/`close()`. Every existing commit site is automatically serialized — no refactor of the 60+ call sites. Reentrancy handled via per-session lock-held flag (commit() internally calls flush() — without the guard, the session would deadlock on its own commit). Read-only sessions never block writers.

- **v0.4.12 — `bulk_persist` ID-shape gate** — rejects `PendingOptimization` rows whose `id` isn't a valid `uuid4`. Production uses `uuid4()` exclusively; non-uuid IDs (`opt-NN-XX`, `tr-NN`) are test-fixture leaks. Logs WARNING so future regressions in test isolation surface loudly.

- **v0.4.12 — Startup GC sweeps** — three new sweeps in `_gc_test_leak_optimizations` + `_gc_reconcile_member_counts` + aggressive `_gc_orphan_probe_runs` (drops 1h TTL gate; ANY `status='running'` probe at startup is dead by definition). Self-healing on every backend restart: deletes test-leak rows (covers BOTH non-uuid id AND non-uuid trace_id patterns), reconciles `PromptCluster.member_count` against actual row counts, marks orphan probes failed.

- **v0.4.12 — CORS-safe global exception handler** — `@app.exception_handler(Exception)` now echoes the request's `Origin` back when in the allowlist + sets `access-control-allow-credentials: true`. Pre-fix, 500 responses shipped without CORS headers and the browser rejected them with "ERR_FAILED" + a misleading CORS error, hiding the actual exception. Body also gains `error_type` (the exception class name) so frontends can render category-aware copy.

- **v0.4.12 — `GET /api/optimize/{trace_id}` defensive query** — used `scalar_one_or_none()` which raised `MultipleResultsFound` when multiple rows shared a trace_id (`trace_id` has no UNIQUE constraint; historical test-fixture leaks created collisions). Now `ORDER BY created_at DESC LIMIT 1` — most-recent row wins deterministically, never 500s.

- **v0.4.12 — SSE keepalive uses real `event: sync` instead of comment** — the SSE endpoint sent `: keepalive\n\n` (a comment) which browsers consume at the TCP layer for connection-keepalive but DO NOT fire any JS event handler for. The frontend's staleness detector saw "no events for 90s" during long-running probes (LLM calls in subprocesses, zero DB activity → zero event_bus publishes) and falsely reported the connection as disconnected while it was actually healthy. Now emits a real `event: sync` every 30s carrying `current_sequence` + `keepalive: true`. Connection stays "healthy" indefinitely regardless of write-side traffic.

- **v0.4.12 — ENRICHMENT layer count matches visible dot count** — pre-fix the panel header showed "X/9 layers" computed from `Object.keys(context_sources).length` (which includes metadata keys like `source`, `batch_id`, `agent` plus the 4 boolean source flags), but only the 4 layers in `LAYER_ORDER` got dot indicators. Result for a typical seed prompt: header read "7/9", eye counted 4 dots → cognitive mismatch. Now promotes the 5 telemetry signals that already had dedicated detail sections below the dot list (RETRIEVAL, STRATEGY RANKINGS, DOMAIN SIGNALS, TASK-TYPE SCORES, CONTEXT INJECTION) to first-class dots in the layer list. Each `LAYER_ORDER` entry carries an `isActive` predicate that pulls from the right slice of the enrichment payload. Header counts are computed from `LAYER_ORDER` itself, so "X/Y" is guaranteed to match the visible dot count by construction.

- **v0.4.12 — Reactive bulk-delete UI** — `HistoryPanel.confirmBulk` now optimistically filters deleted ids out of `historyItems` immediately on success. Pre-fix, it cleared the selection + closed the modal but waited silently for the SSE `optimization_deleted` round-trip; if SSE was delayed (or the client had momentarily disconnected during a backend restart), rows lingered until manual refresh. SSE listener stays — idempotent filter on already-removed id is a no-op. Network-level failures get a distinct user-facing message ("Network error reaching the server. Check the backend is running and retry.") separate from HTTP 500s ("Server error (ErrType). Retry."). `ApiError.errorType` carries the backend exception class name for category-aware UI handling.

- **v0.4.12 — SSE slow-poll fallback + visibility recovery** — pre-fix, after `MAX_RETRIES=10` exponential-backoff attempts (~111s total), the SSE store entered a permanent "Retries exhausted" state — user had to manually reload the page. Trapped users in a disconnected UI on every multi-restart dev workflow. Now drops into a slow-poll cadence (`SLOW_POLL_DELAY_MS=30000`, same jitter). `retryCapped=true` is now a SIGNAL for the tooltip ("SSE disconnected · Slow-poll retry in Xs"), not a termination state. Visibility-change handler retries immediately when the user returns to the tab and SSE is disconnected.

- **v0.4.12 — Probe cancellation + orphan-recovery hotfix** — discovered during Tier 1 integration validation: a client disconnect mid-stream (curl `--max-time` cut off, FastAPI `ClientDisconnect` raised) cancelled the `ProbeService.run()` async generator, but the `ProbeRun` row stayed in `status='running'` forever — no cleanup, no failure marker. Two fixes: (1) `probe_service.run()` now catches `asyncio.CancelledError`, calls `_mark_cancelled(row)` under `asyncio.shield()` to mark the row `status='failed', error='cancelled'` before re-raising. The shield protects the cleanup commit from immediate re-cancellation. (2) `gc._gc_orphan_probe_runs()` runs at startup, marks rows in `status='running'` for >`PROBE_ORPHAN_TTL_HOURS=1` as failed with `error='orphaned_at_startup'`. Mirrors the existing `_gc_failed_optimizations` pattern. 2 new tests (`TestProbeCancellation`, `TestGCOrphanProbeRuns`).

- **v0.4.12 — Optimizer timeout calibration** — calibrated against live audit-class duration distribution (cycle-19→22 v2 + cycle-23 + Topic Probe Tier 1 integration validation, 2026-04-29):
  - **`ClaudeCLIProvider._CLI_TIMEOUT_SECONDS` 300 → 600s** — per-LLM-call ceiling. Median full-pipeline ~354s, p95 ~480s; the Opus 4.7 OPTIMIZE phase with `xhigh` effort + 80K codebase context can land at 400–500s on its own. Old 300s caused silent retries that surfaced as `network_error: timed out` at the script-level urlopen tier (cycle-23 prompts 3+4, ~590-642s).
  - **`scripts/validate_taxonomy_emergence.py::_post` 600 → 1800s** — covers p99 with 3× headroom for Opus 4.7 task-budget xhigh runs that legitimately need >10 min on 128K outputs.
  - **`scripts/probe.py` httpx client 900 → 3600s** — covers a 10-prompt probe with headroom; longer probes need Tier 2's 202 Accepted + polling architecture (deferred).

### Topic Probe Tier 1 (headline feature)

- **v0.4.12 — Topic Probe Tier 1** — agentic targeted exploration of a user-specified topic against the linked GitHub codebase. Productizes the manual cycle-15→22 workflow that emerged the `embeddings` sub-domain and `data` / `frontend` top-level domains. The user specifies a topic (e.g., "embedding cache invalidation in EmbeddingIndex"), and the agentic system reads the codebase, generates 5–25 code-grounded prompts citing real identifiers, runs them through the optimization pipeline, watches the taxonomy emerge new domains/sub-domains organically, and delivers a structured final report. Topic Probe is a peer of seed agents — same execution primitive (`batch_pipeline`), different generation strategy (LLM-agentic-from-topic-and-codebase vs pre-authored agent template).

  **New surfaces:**
  - `POST /api/probes` (SSE via `StreamingResponse`, IP-keyed `RateLimit("5/minute")`) — 5-phase orchestrator (`grounding → generating → running → observability → reporting`)
  - `GET /api/probes` — paginated `ProbeListResponse` envelope
  - `GET /api/probes/{id}` — full `ProbeRunResult` (404 with `probe_not_found` reason on miss)
  - `synthesis_probe` MCP tool (15th tool, `structured_output=True`) — same 5-phase flow + `ctx.report_progress` per prompt
  - `prompts/probe-agent.md` — hot-reloaded system prompt, 8 template variables (`topic`, `scope`, `intent_hint`, `n_prompts`, `repo_full_name`, `codebase_context`, `known_domains`, `existing_clusters_brief`)
  - `ProbeRun` SQLAlchemy model + idempotent Alembic migration (`ec86c86ba298`, uses `inspector.get_table_names()` guard per codebase convention)
  - 7 new `probe_*` taxonomy events (`probe_started`, `probe_grounding`, `probe_generating`, `probe_prompt_completed`, `probe_taxonomy_change`, `probe_completed`, `probe_failed`)
  - `current_probe_id` ContextVar (declared in `probe_service.py`, re-exported by `probe_event_correlation.py`) injects `probe_id` into existing taxonomy events fired during a probe — backward-compat additive, consumers tolerate absent keys
  - `scripts/probe.py` — CLI shim translating `validate_taxonomy_emergence.py::PROMPT_SETS` presets → `POST /api/probes` (backward-compat with v0.4.10/v0.4.11 chain runners; `PROMPT_SETS` dict unchanged)

  **New configuration:**
  - `PROBE_RATE_LIMIT: str = "5/minute"` — IP-keyed POST `/api/probes` rate limit
  - `PROBE_CODEBASE_MAX_CHARS: int = 40_000` — codebase context budget for the agentic generator (half of `INDEX_CURATED_MAX_CHARS=80000`; topic-focused probes don't need full repo budget)
  - `BATCH_CONCURRENCY_BY_TIER: dict[str, int]` extracted as module-level constant in `batch_orchestrator.py` (was inline `max_parallel: int = 10` parameter; values unchanged: internal=10, api=5, sampling=2)

  **Calibration-aware assertion thresholds** (per `docs/specs/topic-probe-2026-04-29.md` § 5, grounded in cycle-19→22 v2 replay distribution):
  - Mean overall (analysis): >= 6.9 (mean − 1σ; σ ≈ 0.5)
  - p5 overall: >= 6.5 floor
  - F5 false-premise flag fire rate: <= 1 per probe on healthy corpus
  - F4 strategy fidelity: 100% (every persisted `strategy_used` ∈ available strategies)

  **8 TDD cycles** (RED → GREEN → REFACTOR → code-review per cycle; substantive REFACTORs across all 8 per memory `feedback_tdd_protocol.md`):
  - C1 (probe-agent.md template) — 5 ACs / 6 tests; REFACTOR canonicalized manifest shape + tightened `or` assertion
  - C2 (ProbeRun model + migration) — 4 ACs; REFACTOR extracted shared `enable_sqlite_foreign_keys` fixture (deduplicated 5 tests)
  - C3 (probe_generation primitive) — 5 ACs / 6 tests; REFACTOR aligned backtick regex with F1 specificity heuristic byte-for-byte + added operational logging
  - C4 (probe_service 5-phase orchestrator) — 6 ACs; REFACTOR re-aligned 4 of 6 plan-vs-reality adaptations (n_prompts ≥5 floor, probe_generation delegation, partial-mode fixture leak, defensive resolve_project_id wrap)
  - C5 (routers/probes.py REST surface) — 6 ACs / 7 tests; REFACTOR eliminated `model_construct` validation bypass + relocated `get_probe_service` to `dependencies/probes.py`
  - C6 (synthesis_probe MCP tool) — 5 ACs; REFACTOR extracted shared `build_probe_service` constructor (eliminated REST/MCP factory duplication)
  - C7 (SSE events + `probe_id` correlation) — 4 ACs; REFACTOR hoisted 6 inline event_logger imports to module level
  - C8 (CLI shim) — 3 ACs; REFACTOR audited (no substantive change warranted at Tier 1)

  **38 ACs total / 39 tests across 8 test files.** Full backend suite: 3232 passed + 1 skipped (was 3191 + 1, +41 net new tests). ruff + mypy clean. Spec: `docs/specs/topic-probe-2026-04-29.md` (gitignored). Plan: `docs/plans/topic-probe-tier-1-2026-04-29.md` (gitignored).

  **All 4 Topic Probe tiers will ship within the 0.4.x line:** Tier 1 = v0.4.12 (this release), Tier 2 = v0.4.15 (save-as-suite + replay + UI navigator — bumped repeatedly: v0.4.13 → v0.4.14 → v0.4.15 as each prior release was reallocated to ship architectural fixes), Tier 3 = v0.4.16 (cross-tier composition: probe → seed-agent promotion, drill-into-cluster from seed run), Tier 4 = v0.4.17 (substrate unification: SeedRun and ProbeRun collapse to one model).

## v0.4.11 — 2026-04-28

### Fixed

- **v0.4.11 P0a — Domain proposal cluster-count floor** — `engine._propose_domains()` now requires ≥`DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS=2` distinct contributing clusters before promoting a top-level domain. Closes the "ghost domain" pathology surfaced live by the cycle-19→22 v2 replay where `fullstack` was promoted from a single cluster of 3 prompts (67% consistency, coherence 0.0/skipped), then merged out leaving an empty domain node frozen by the 48h dissolution gate. Both proposal paths enforce the floor: per-cluster pass refactored to aggregate by `top_primary` BEFORE creating domains (rejects primaries with only 1 contributor); pooled pass adds `len(bucket["clusters"]) >= MIN_SOURCE_CLUSTERS` check alongside the existing pooled-member gate. Rejected primaries emit `proposal_rejected_min_source_clusters` event with `{domain_label, source_cluster_count, required_min, source}` for forensic visibility. Module-level invariant assertion in `_constants.py` fails fast on `< 1` configuration drift. Test fixtures in `test_domain_discovery.py` updated to spread members across 2 clusters where they previously assumed single-cluster promotion as a happy-path artifact. Spec: `docs/specs/domain-proposal-hardening-2026-04-28.md` §P0a.

### Added

- **v0.4.11 P1 — Operator dissolve-empty endpoint** — `POST /api/domains/{id}/dissolve-empty` (10/min) lets operators force-dissolve ghost domains (`member_count == 0`, age >= `DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES=30`) without waiting for the 48h `_reevaluate_domains` gate. Idempotent (200 + `dissolved=False, reason="already_dissolved"` on re-call); 409 + `reason="not_empty"` if domain has members; 409 + `reason="too_young"` if too recent; 404 if not found; 429 on rate limit. Mirrors v0.4.8 R6 (`POST /api/domains/{id}/rebuild-sub-domains`) — operator escape hatch pattern. Emits `domain_ghost_dissolved` decision event + `taxonomy_changed` SSE on success. Provides immediate operational drain for the existing live `fullstack` ghost (id=`139befac`) without the 46h wait. Spec: `docs/specs/domain-proposal-hardening-2026-04-28.md` §P1.

## v0.4.10 — 2026-04-28

### Fixed

- **F3.1 (v0.4.10) — Persistence wiring for analysis-weighted overall score** — v0.4.9 F3 wired analysis-aware `compute_overall(task_type)` into `score_blender.blend_scores` and the improvement_score loops, but the actual stored `overall_score` field reads from `DimensionScores.overall` (the `@property`), which always uses the default `DIMENSION_WEIGHTS`. The analysis weights were computed but never reached the database. **Cycle-19→22 replay confirmed the bug**: stored mean **7.155** = v3 default schema; computed-with-v4 mean **7.208**. Delta lost: **+0.053** across 19 prompts. Fix: every persistence site (DB write, SSE event payload, log line, `PipelineResult` build) where `task_type` is in scope now calls `optimized_scores.compute_overall(task_type)` instead of `optimized_scores.overall`. Touch points: `pipeline_phases.py` (5 sites), `sampling_pipeline.py` (4 sites), `batch_pipeline.py` (1 site), `pipeline.py` (2 sites). Refinement service untouched — refinement does not re-classify task_type and degrades to `None` (default-weighted) which preserves prior behavior. New regression suite `TestPersistenceWeightWiring` (3 tests): pins the divergence between `BlendedScores.overall` (analysis-weighted source of truth) and `DimensionScores.overall` (default-weighted property), verifies `compute_overall(task_type)` recovers the analysis-weighted value, and asserts via code inspection that no persistence site uses the bare `.overall` property when `task_type` is in scope. Full suite 3180 passed + 1 skipped (was 3177 + 1, +3 new).

## v0.4.9 — 2026-04-28

### Changed

- **F1 — Specificity heuristic now credits backtick-wrapped code identifiers** — added an 11th category `(r"`[a-zA-Z_][a-zA-Z0-9_./:-]*`", 0, 2.0)` to `heuristic_specificity` so audit prompts citing real code references (`` `engine.py` ``, `` `_reevaluate_sub_domains` ``, `` `cluster_metadata.generated_qualifiers` ``) earn structural specificity credit. Previously these scored 0 from the citations despite being structurally more specific than vague feature prompts. Live: 3-backtick prompt scored 4.6 vs 3.0 (delta +1.6, well above the 0.5 acceptance floor). Cap=2.0 matches all other structural categories — 3 hits contribute +1.6, 5+ hits saturate. (audit `docs/audits/audit-prompt-class-deep-dive-2026-04-27.md` F1, `backend/app/services/heuristic_scorer.py:251-252`)
- **F2 — `ZSCORE_MIN_STDDEV` raised 0.3 → 0.5** — bypasses z-norm on narrow-distribution task types like audit-class prompts (typical stddev ~0.35). Pre-fix, an audit prompt with adequate LLM=6.9 floor-capped to 2.5 because z=−0.857 against the audit-class mean. Mirrors the narrow-distribution flag already in place at `routers/health.py:392-394`. 3 historic test fixtures bumped 0.5→0.6 to preserve their fire-on-stddev assertion semantics. (audit F2, `backend/app/services/score_blender.py:36-39`)
- **F3 — `DIMENSION_WEIGHTS` is now per-task-type via `get_dimension_weights(task_type)`** — analysis task type uses clarity 0.25 / specificity 0.25 / structure 0.20 / faithfulness 0.20 / conciseness 0.10, recognizing that audit prompts have inherently different priorities than feature prompts (clarity/specificity/structure carry the substance; faithfulness premises are often hypotheses; conciseness is structurally lower since audits enumerate findings). `SCORING_FORMULA_VERSION` bumped 3 → 4. `DimensionScores` gains a sibling `compute_overall(task_type=None)` method alongside the preserved `@property def overall` so ~30 backward-compat call sites without `task_type` in scope remain stable. Threading covers `score_blender.blend_scores`, `pipeline_phases.persist_and_propagate` (both improvement_score loops), `batch_pipeline`, `sampling_pipeline`. Module-level R8-style invariant assertions in `pipeline_contracts.py` fail-fast if any future edit breaks the sum-to-1.0 property of either schema. (audit F3, `backend/app/schemas/pipeline_contracts.py` + 5 service files)

### Fixed

- **F4 — Pipeline strategy fidelity** — `OptimizationResult.strategy_used` removed from the LLM output schema. The optimizer LLM no longer declares a strategy; the persisted `strategy_used` is always the resolved `effective_strategy` from `resolve_effective_strategy()`. Pre-fix, trace `42097cf8` had `effective_strategy='meta-prompting'` but persisted `strategy_used='chain-of-thought'` because the LLM freelance-chose CoT. Pydantic `extra="forbid"` now rejects any LLM emission of the field. Refinement service ripple-fix: `refinement_service.py:428,460` switched from `refined.strategy_used` to `strategy_name` (orchestrator-side variable from `prev_turn.strategy_used`). DB column `Optimization.strategy_used` is unchanged — was already populated from `effective_strategy`. (audit F4, `backend/app/schemas/pipeline_contracts.py:154-156` + `pipeline.py:476` + `sampling_pipeline.py:576+588` + `batch_pipeline.py:443` + `refinement_service.py:428,460`)

### Added

- **F5 — `possible_false_premise` divergence flag** — fires when an analysis-class prompt scores LLM faithfulness < 5.0 AND `technical_dense=True` (≥ `TECHNICAL_CONTEXT_THRESHOLD=3` technical nouns in the prompt). Surfaces audits whose surface symbol density may be masking a wrong premise the LLM detected against ground truth. Purely additive — does not change scores, only telemetry. Discovered via Phase B forensic trace where prompt `c4da176c` claimed Phase 4.95 had a "cluster signature gate" — the actual code at `warm_path.py:456-466` has no such gate, but the surface technical density passed the existing `>2.5pt` divergence detector. (audit F5, `backend/app/services/score_blender.py:222-232`)
- **Cycle-23 audit-prompt validation set** — 5 audit-class prompts in `scripts/validate_taxonomy_emergence.py` exercising F1 backtick credit, F3 analysis weights, F4 strategy fidelity, F5 false-premise flag (one prompt with deliberately wrong premise). Replays the cycle-22 audit corpus shape post-v0.4.9.

## v0.4.8 — 2026-04-27

### Fixed

- Hardened sub-domain dissolution: Bayesian shrinkage on consistency metric prevents premature dissolution at small member counts (audit `docs/audits/sub-domain-regression-2026-04-27.md` R1).
- Sub-domain dissolution grace period extended from 6h to 24h (audit `docs/audits/sub-domain-regression-2026-04-27.md` R2).
- Sub-domain dissolution now skips re-evaluation (with telemetry event `sub_domain_reevaluation_skipped`) when the sub-domain's `generated_qualifiers` snapshot is empty, preventing fall-through to legacy exact-equality matching (audit `docs/audits/sub-domain-regression-2026-04-27.md` R3).

### Changed

- Sub-domain re-evaluation matching cascade extracted to a shared pure primitive `match_opt_to_sub_domain_vocab` in `sub_domain_readiness.py` (audit `docs/audits/sub-domain-regression-2026-04-27.md` R4).
- `_constants.py` now asserts the threshold-collision invariant (`SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW > SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`) at module-load time via `_validate_threshold_invariants()`; degenerate configurations fail-fast (audit `docs/audits/sub-domain-regression-2026-04-27.md` R8).

### Added

- Sub-domain dissolution events now carry forensic detail: `matching_members` count + up to 3 `sample_match_failures` per `sub_domain_dissolved`/`sub_domain_reevaluated` event with cluster_id, domain_raw, intent_label, reason (audit `docs/audits/sub-domain-regression-2026-04-27.md` R5).
- Operator endpoint `POST /api/domains/{id}/rebuild-sub-domains` for sub-domain recovery with optional threshold override and dry-run support; emits `sub_domain_rebuild_invoked` telemetry and `taxonomy_changed` event when sub-domains are created (audit `docs/audits/sub-domain-regression-2026-04-27.md` R6).
- Vocab regeneration events (`vocab_generated_enriched`) now carry `previous_groups`, `new_groups`, `overlap_pct` (Jaccard) for forensic correlation with downstream sub-domain dissolutions; emits a WARNING log when overlap < 50% on a non-bootstrap regen (audit `docs/audits/sub-domain-regression-2026-04-27.md` R7).

## v0.4.7 — 2026-04-26

### Fixed

- **TF-IDF cascade source 3 was structurally silent — domain-node aggregation + score normalization** — the qualifier cascade's third source (`tf_idf` → `signal_keywords`) showed `0` hits across every domain in live readiness telemetry for ~5 days. Two stacked bugs: (1) `_extract_domain_keywords()` queried `Optimization.cluster_id == cluster.id`, but **domain nodes never own optimizations directly** — opts live in their child clusters. Empirically: 0 rows for the backend domain id vs 35 rows when joined via `parent_id`. So every signal refresh persisted `signal_keywords=[]`. (2) Even after the query fix, raw TF-IDF mean scores topped out at 0.167 on the live backend corpus — but the cascade admit gate requires `weight >= 0.5`, so every keyword was filtered out. Fix shape: `_extract_domain_keywords()` branches on `cluster.state == "domain"` and aggregates `raw_prompt` text across descendant active/mature clusters; output min-max normalized so the top keyword weighs `1.0` and the K-th preserves the relative ratio. Live verified after force refresh: backend produces 15 keywords, 8 pass the >=0.5 gate; cascade source 3 immediately surfaces `audit` (3 hits, all from `tf_idf`) for backend and `refresh`/`write` for general — opts that were previously **uncounted** now flow through. 3 regression tests pin domain-node descendant aggregation, regular-cluster path preservation, and empty-corpus safety. (`backend/app/services/taxonomy/engine.py`)
- **Vocab regeneration was blind to TF-IDF orphans + group continuity** — `generate_qualifier_vocabulary()` only saw cluster intent labels, qualifier distribution, and centroid similarity. Latent themes that the cascade was recording exclusively via source 3 (e.g., `audit` on backend) had no path back into the curated Haiku vocab — they'd accumulate forever and never get absorbed. Fix shape: two new optional kwargs threaded into the call site — `domain_signal_keywords: list[tuple[str, float]]` (top TF-IDF terms with normalized weights) + `existing_vocab_groups: list[str]` (previous group names for continuity). The system prompt instructs Haiku to evaluate orphan terms (real specialization vs lexical noise) and to prefer existing names when geometry hasn't shifted. Substring-coverage filter at the rendering layer drops orphans already covered by a current group; `domain_label` itself is excluded. Closes the organic feedback loop: TF-IDF surfaces theme → cascade records it → next vocab regen sees it → Haiku decides absorb-or-ignore → next cycle records via source 2 instead of source 3. No user observation or manual intervention required. (`backend/app/services/taxonomy/labeling.py` + `engine.py`)
- **MCP routing thrashed on every Claude Code tool call (5 root-cause fixes)** — Claude Code (and similar MCP clients) opens and closes SSE sessions per tool call, producing visible status-flicker every 30–110s + 994 cross-process publishes per cycle. **Issue 1**: `routing.disconnect_averted` log demoted INFO → DEBUG (cycle 11 produced 32 of these in 30 minutes drowning real signal). **Issue 2** (debounce): `on_mcp_disconnect()` mutates state immediately but defers the broadcast for `DISCONNECT_DEBOUNCE_SECONDS=3.0` via `asyncio.create_task(_deferred_disconnect_broadcast())`; a re-initialize within the window cancels the pending task. **Issue 3** (initialize suppression): `_pre_disconnect_sampling` snapshot taken at disconnect time; `on_mcp_initialize` compares against it on debounce-cancel and suppresses the matching initialize broadcast when capability is unchanged — halves per-tool-call publish volume. **Issue 4** (recovery trust-fresh-file): `_recover_state()` now trusts a session file when `is_capability_fresh AND not detect_disconnect`, eliminating the ~60s blackhole window when the FastAPI backend restarts (uvicorn auto-reload) while the MCP server process keeps running with an active sampling-capable client. **Issue 5** (operational): `--reload-dir app` already correctly scoped — prompt edits don't trigger reload. 3 new regression tests for debounce + suppression + sustained-disconnect commits; 2 existing recovery tests updated to reflect intentional new behavior; `test_capability_fresh_but_disconnected` added for the `sse_streams=0` negative case. 178 routing+sampling+migration tests pass. (`backend/app/services/routing.py` + tests)
- **Migration `d3f5a8c91024` (heuristic_baseline_scores + pattern usefulness counters) was not idempotent** — re-running raised `duplicate column name: heuristic_baseline_scores` because the migration unconditionally added all three columns. Both migration tests (`test_hotpath_indices_migration::test_upgrade_is_idempotent` + `test_template_migration::test_migration_is_idempotent`) failed pre-fix. Fix: wrapped each `add_column` in `inspector.get_columns()` guards (also down-path); pattern matches the `c2d4e6f8a0b2` reference. (`backend/alembic/versions/d3f5a8c91024_heuristic_baseline_pattern_usefulness.py`)
- **Topology halo + readiness ring lag** — formation-animation callback wasn't syncing halo + ring positions per frame, so they'd visually trail cluster nodes until a zoom/camera nudge forced a redraw. Added per-frame sync inside the formation animation tick. (`frontend/src/lib/components/taxonomy/SemanticTopology.svelte`)
- **Frontend Observatory didn't refresh pattern-density or timeline on `taxonomy_changed`/`domain_created` / SSE-reconnect** — `observatoryStore.refreshPatternDensity()` + `loadTimelineEvents()` now fire from those handlers in `+page.svelte`, so the Heatmap + Timeline reflect cluster mutations without a manual reload. (`frontend/src/routes/app/+page.svelte`)
- **B5+ writing-about-code path: task-type lock + differentiated codebase trim** — three regressions stacked when a writing/creative prompt has technical anchors in its first sentence (e.g., `` `docs/CHANGELOG.md` ``, `` `POST /api/clusters/match` ``). The B2 first-sentence rescue catches these via `has_technical_nouns` and routes them to `code_aware`, but the LLM analyzer often flips the `task_type` from `writing` to `coding`, the codebase-context layer delivers the full 80K-char curated retrieval, and the optimizer hallucinates against related-but-wrong code (live regression: cycle-10 CHANGELOG scored 6.61 vs 6.96 without codebase context). Fix shape: (1) **B5+ task-type lock** in `pipeline_phases.resolve_post_analyze_state` — when the prompt's first word is a writing lead verb (`write/draft/compose/author/summarize/describe/document/outline/narrate`), the heuristic also said writing, and the LLM said coding, prefer the lead-verb signal. The `write` verb additionally requires a prose-output cue in the first sentence (`changelog/section/style/release/page/reference/...`) to avoid false positives on `Write a function that...`. (2) **B5 full-prompt rescue** in `context_enrichment.enrich()` — when the first-sentence rescue MISSED but the task is writing/creative AND the body has technical content, scan the whole prompt with `has_technical_nouns` and upgrade the profile to `code_aware`. Sets `full_prompt_technical_rescue=True` for observatory tooling. (3) **B5+ codebase trim** — when `_writing_about_code` (heuristic OR lead-verb writing AND repo linked AND tech signals), cap codebase context at `WRITING_CODE_CONTEXT_CAP_CHARS=15000` instead of the default 80K. Tracks `rescue_path` (`B2_first_sentence` vs `B5_full_prompt`) and `trigger` (`heuristic_task_type` vs `lead_verb`) for forensic auditing. End-to-end live verification: B2 path scored 7.39 (vs pre-fix 6.61), B5 path scored 7.33 with `concision_preserved=True` and `length_ratio=2.73` (was 3.32). (`backend/app/services/context_enrichment.py` + `pipeline_phases.py`)
- **C1/C5 asymmetric z-score cap + C2 length budget + C3 technical conciseness blend** — score-blending calibration sweep against the live distribution: (C1) z-score normalization clipped extreme positives at `+2.0` and floor at `-2.0`, but the data showed legitimate above-average prompts compressing toward the mean while below-floor outliers should be capped. **C5 makes the cap asymmetric** — floor only at `-2.0`, ceiling uncapped, preserving upside signal. (C2) optimizer over-expanded prompts when conciseness scored low; analysis_summary now includes length-budget guidance based on `_orig_conc` and `_orig_len`. (C3) technical prompts repeat domain vocabulary which TTR penalizes — bumped the conciseness blend to 0.35 for high technical-noun-density prompts so the LLM signal weighs more for that category. Existing `test_extreme_positive_is_clamped_at_ten` / `test_extreme_negative_is_clamped_at_one` updated to reflect the asymmetric cap. (`backend/app/services/score_blender.py` + `pipeline_phases.py`)
- **C6 selective inline-backtick handling + brand vocab expansion** — first-sentence extraction stripped ALL backticks, including ones around real code references like `` `POST /api/clusters/match` `` (which carry signal). Made stripping selective via `_looks_like_code_reference` — unwraps when the contents contain `/`, `_`, or a source-extension; strips otherwise. Pruned ambiguous brand names from `_TECHNICAL_NOUNS` (github, lambda, react, vue, kafka, mocha, cypress) to reduce false-positive PascalCase identifier triggers; expanded with ~50 unambiguous technical terms (oauth, jwt, fernet, dockerfile, kubectl, redis, postgresql, asyncio, semaphore, savepoint, etc.). (`backend/app/services/task_type_classifier.py`)

### Added

- **`heuristic_baseline_scores` deterministic baseline (C4)** — new JSON column on `Optimization` storing `HeuristicScorer.score_prompt(raw_prompt)` directly. Distinct from `original_scores` (which is LLM+heuristic blended and contaminated by A/B presentation noise). `improvement_score` derivation now uses `heuristic_lift` so deltas reflect real optimization gain rather than scoring-method drift. Migration `d3f5a8c91024_heuristic_baseline_pattern_usefulness` adds the column + the `useful_count`/`unused_count` counters on `OptimizationPattern` (T1.3-lite). (`backend/app/models.py` + `pipeline_phases.py` + alembic)
- **T1.1 Bayesian shrinkage on phase-weight learning** — `compute_score_correlated_target()` now uses `SCORE_ADAPTATION_PRIOR_KAPPA=8.0` shrinkage prior with `SCORE_ADAPTATION_MIN_SAMPLES=2`. Replaces the previous `min 10 samples` hard gate that left small clusters perpetually unable to learn. Convergence is slower for tiny clusters but never stuck; large clusters dominate the prior and learn fast. Test `test_score_adaptation_min_samples` updated to assert `==2` and add `KAPPA` assertion. (`backend/app/services/taxonomy/fusion.py`)
- **T1.6 MMR diversity on few-shot retrieval** — Maximal Marginal Relevance with `FEW_SHOT_MMR_LAMBDA=0.6` re-ranks the dual-similarity merge (input-similar + output-similar) so the few-shot examples shown to the optimizer cover diverse modes rather than clustering near the prompt embedding. Same total budget — better diversity per token. (`backend/app/services/pattern_injection.py`)
- **T1.3-lite pattern usefulness counters** — `record_pattern_usefulness()` increments `OptimizationPattern.useful_count` when host overall score ≥ `PATTERN_USEFUL_FLOOR=7.5` and `unused_count` when ≤ `PATTERN_UNUSED_CEILING=6.5`. Feeds future pattern-quality decay + GlobalPattern lifecycle. (`backend/app/services/pattern_injection.py` + migration)
- **T1.7 A4 confidence gate tuned** — `_LLM_CLASSIFICATION_CONFIDENCE_GATE=0.40` + `_LLM_CLASSIFICATION_MARGIN_GATE=0.10` replace the previous `0.7`/no-margin gate. A4 Haiku fallback now fires more often (~15-20% vs ~5%) for genuinely ambiguous prompts where the heuristic margin is small. (`backend/app/services/task_type_classifier.py`)
- **`prompts/optimize.md` prompt-aware rationale guideline** — the optimizer was producing generic "Audit alone is ambiguous" rationales instead of crediting the user's signals. New guideline drives optimizer to cite specific cues (verify/Confirm/check) from the input prompt when explaining changes. (Hot-reloaded.)
- **3 new TF-IDF regression tests** — `test_extract_domain_keywords_aggregates_descendants_for_domain_node` + `test_extract_domain_keywords_regular_cluster_unchanged` + `test_extract_domain_keywords_empty_corpus_returns_empty` pin the dual-mode behaviour and normalization invariant. (`backend/tests/taxonomy/test_domain_discovery.py`)
- **3 new routing-debounce regression tests** — `TestDisconnectDebounce` covers the per-tool-call cycle (no events emitted), capability-changed initialize broadcasts, and sustained-disconnect commits after the debounce window. (`backend/tests/test_routing.py`)
- **`docs/self-learning-audit-2026-04-25.md`** — 488-line audit covering self-learning capacities, observed score trajectory, Tier 1–4 recommendations.
- **Validation harness extended (cycles 5–11)** — `scripts/validate_taxonomy_emergence.py` gains 6 additional cycle prompt sets covering diverse domains (security audit, devops production-readiness, database query optimization, frontend cache invalidation, docs CHANGELOG drafting, embedding hot path tracing).

### Changed

- **Test mocks for `generate_qualifier_vocabulary`** — three test fakes in `tests/taxonomy/test_sub_domain_lifecycle.py` accept `**_kwargs` so they tolerate the additive `domain_signal_keywords` + `existing_vocab_groups` parameters. Test-only signature drift, no runtime impact. (891/891 taxonomy tests pass.)

## v0.4.6 — 2026-04-25

### Added
- **Self-update hardening — pre-flight endpoint + drain lock + auto-stash + per-step progress (PR upcoming)** — comprehensive rework of the auto-update flow against three P0 risks identified in the end-to-end audit: (1) **strategy edits silently lost** when a user runs `git checkout --force` after the dirty-tree error; (2) **in-flight optimization race** during restart corrupting the partially-written DB row; (3) **local commits ahead of origin/main silently orphaned** by pure-tag checkout. Six new pieces ship together: (a) `customization_tracker.py` records every `PUT /api/strategies/{name}` edit into `data/.user_customizations.json` (gitignored, atomic-write, schema-versioned, defensively coerces corrupt registries to empty); (b) `GET /api/update/preflight` returns a `PreflightStatus` with dirty-file source classification (`user_api` / `manual_edit` / `untracked`), commits-ahead-of-origin, in-flight optimization count, detached-HEAD detection, target-tag presence — frontend calls this BEFORE enabling the apply button so the user sees the full picture; (c) `UpdateInflightTracker` singleton coordinates pipeline `begin/end(trace_id)` with `apply_update`'s drain wait (60s budget, 0.5s poll), and the `optimize` router returns 503 when `update_in_progress=True` so new requests don't get killed mid-pipeline; (d) **auto-stash + pop** in `apply_update` — `git stash push -m synthesis-update-{tag} prompts/` before checkout, `git stash pop` (matched by prefix sentinel) after, with stash-pop conflicts surfaced via `update_complete.stash_pop_conflicts` so the user sees exactly which files need manual resolution; (e) **per-step SSE events** — `update_step` emitted at every phase (`preflight / drain / fetch_tags / stash / checkout / deps / migrate / pop_stash / restart / validate`) with `running / done / warning / failed` status — frontend renders a per-step timeline replacing the unused `updateStep` placeholder; (f) **branch-ahead + detached-HEAD warnings** in preflight; **`force=true`** on `apply` bypasses warnings (commits-ahead, in-flight remaining after drain) but never blocking issues (non-prompt uncommitted changes, invalid tag). Frontend rebuild: `UpdateBadge` dialog now renders the preflight panel with `dirty-source` color-coded chips, dirty-file paths in a collapsed details, in-flight optimization counter, auto-stash tag; the apply button is gated on `can_apply`; warnings get a "Update & Restart (force)" variant; updating-state badge shows the current step name; new `update-dialog.completion` view renders `validationChecks` (was populated but never displayed) + `stashPopConflicts` recovery list; new retry button after the 120s health-poll timeout instead of stranding the user. 17 customization-tracker unit tests + 17 update_service tests (drain lifecycle, preflight readiness gates, auto-stash sentinel, round-trip stash preservation, concurrent-update block) cover the new surface. Backend full suite 3094 passed.

## v0.4.5 — 2026-04-25

### Fixed
- **Pattern-injection provenance now writes post-commit (PR #55, e2e validation 2026-04-25)** — the internal/sampling pipeline called `auto_inject_patterns()` BEFORE the parent `Optimization` row was committed (the patterns must flow into the optimizer prompt), so every inline `OptimizationPattern` insert hit a `FOREIGN KEY constraint failed` inside its `begin_nested()` SAVEPOINT and silently rolled back. Production DB had **0 `relationship='injected'` rows over a full 21-prompt validation cycle** while the SSE stream + `applied_pattern_texts` field correctly captured live injection. Effect: `injection_effectiveness` health metric had no provenance to compute lift against; `OptimizationPattern.global_pattern_id` FK was unused; cross-cluster pattern propagation missed the injection signal that feeds GlobalPattern promotion. Fix shape: `auto_inject_patterns()` gains `record_provenance: bool = True` (passthrough/refine keep in-line behavior); internal/sampling call with `record_provenance=False`; new public `record_injection_provenance()` extracted as standalone helper; `persist_and_propagate()` invokes it after `db.commit()`. Live verified: latest optimization writes `injected: 3` rows alongside the canonical `source: 21` rows. 4 new unit tests covering `record_provenance=False` skip-inline + topic / global / cross-cluster / dedup paths of the helper.
- **Enrichment profile no longer demotes async/concurrency code prompts to `knowledge_work` (PR #55)** — three compounding bugs caused code-aware prompts about a linked codebase to silently skip codebase context, strategy intelligence, AND pattern injection: (1) `_TECHNICAL_NOUNS` had no async/concurrency vocabulary (`asyncio`, `coroutine`, `eventloop`, `mutex`, `semaphore`, `deadlock`, `savepoint` added); (2) the `has_technical_nouns()` whitespace tokenizer didn't split interior `.`/`-`/`/` so `asyncio.gather`, `cache-aware`, `backend/app/services/...` left `asyncio`/`cache`/`backend` unreachable; (3) the snake_case (`_spawn_bg_task`, `link_repo`) and PascalCase (`EmbeddingService.embed_single`) identifier-syntax forms had no detector at all. The PascalCase branch is structural-marker-gated to avoid false positives on prose brand mentions (`JavaScript`, `TypeScript`, `GitHub`, `YouTube`, `McDonalds`, `PostScript`) — bare PascalCase requires a companion `.`/`/`/`-` in the same token. The first-sentence boundary regex was also tightened from `re.split(r"[.?!]")` to `re.split(r"[.?!](?=\s|$)")` so module-method dots no longer truncate the boundary at the wrong place. Live re-fire of the demoted prompt confirms `code_aware` profile + 30 patterns injected (was 10) + score 8.89 (was 8.81). 14 new tests across vocab, identifier syntax, brand-name negative cases, and sentence-boundary integrity.
- **GitHub OAuth no longer surfaces upstream JSON failures as misleading CORS errors (PR #55)** — all 4 OAuth call sites (`/auth/device`, `/auth/device/poll`, `/auth/login` callback, internal `_refresh_access_token`) called `resp.json()` unconditionally on the GitHub response. When GitHub returned an empty body / HTML error page during a transient outage, the unhandled `JSONDecodeError` bubbled up inside the CORS middleware response-prep chain — the resulting 500 had no `Access-Control-Allow-Origin` header and the browser surfaced it as `"blocked by CORS policy"`. New `_safe_github_json()` helper turns malformed responses into a clean `HTTPException(502)` that flows through CORS normally with the right header set. 4 new unit tests (decode-error, empty body, non-object JSON, valid-dict happy-path).
- **`init.sh status` banner now reflects live routing state, not bridge-install state** — the `_resolve_active_tier()` heuristic claimed `Active tier: sampling` whenever the VS Code bridge was installed AND the backend's MCP port answered, regardless of whether VS Code was actually open. Real backend routing cycled `passthrough → internal` correctly under the hood; only the banner was sticky. Fix: query `GET /api/providers` and use `routing_tiers[0]` (the backend's authoritative live priority). The `RoutingManager.available_tiers` property only includes `"sampling"` when `sampling_capable=True AND mcp_connected=True`. Falls back to a heuristic (matching the same priority order the backend uses) only when curl/python3 are unavailable during the brief pre-startup window.
- **Optimizer-thinking interrogative-voice no longer leaks into the optimized prompt (PR #55)** — user-observed regression: items 1–3 of an audit prompt's "Closed taxonomy of races" section ended in INTERROGATIVE phrasing (``Where is dispose() actually awaited?``, ``Does any code path capture an AsyncSession across an await?``) — the optimizer's analytical thinking ("things I'd want to check") leaking into what should be directive instructions to the executor's LLM. Items 4–5 of the same prompt correctly used IMPERATIVE voice (``Confirm…``, ``Distinguish…``). Fix: `prompts/optimize.md` gains a "Imperative voice for instructions, not interrogative" guideline with bad/good examples drawn directly from the regression; `prompts/strategies/meta-prompting.md` gains a "Voice discipline" section reinforcing the rule under the strategy that produced the leak (the screenshot showed `meta-prompting`). Both make clear: trailing rhetorical questions in instruction lists belong in `changes_summary` rationale, never in the deliverable prompt itself. (Hot-reloaded — no restart required.)
- **Post-LLM domain reconciliation — qualifier syntax now flows into `domain_raw` (PR #55, e2e validation 2026-04-25)** — two compounding architectural gaps caused 11 of 13 cycle-3 prompts (tracing/instrumentation themed) to land with bare `domain_raw="backend"` even though the cached organic vocabulary had `≥2` keyword hits per prompt against the `instrumentation`/`tracing` qualifier groups: (1) `domain_raw` was set unconditionally from `analysis.domain` (the LLM's analyze-phase output), so `_enrich_domain_qualifier()` running in the heuristic-only path did nothing — the heuristic's qualifier-enriched output was overwritten by the LLM's bare primary; (2) the LLM follows analyze.md's two parallel instructions inconsistently — colon-style (`backend: observability`) parses correctly, but hyphen-style (`backend-observability`, the syntax analyze.md mandates for invented sub-domains) was misparsed as a brand-new primary by `parse_domain()` because it splits only on `:`. Live impact: cycle-3 prompt #7 (score 9.0) created a new orphan cluster under `general` with `domain="backend-observability"` instead of joining the `backend` subtree. Fix shape: new `_normalize_llm_domain(domain, known_primaries)` helper rewrites `backend-observability` → `backend: observability` when the prefix is a registered primary; `enrich_domain_qualifier()` now also runs **post-LLM** in `resolve_post_analyze_state()` to layer organic qualifiers onto bare LLM primaries. Both transforms are additive (idempotent, only apply when no `:` present, `domain_resolver`-gated). 8 new unit tests on the helper covering hyphen-with-known-primary normalization, hyphen-with-unknown-primary preservation, colon-already-present idempotence, multi-word qualifier preservation, trailing-hyphen edge case, and round-trip idempotence.
- **Qualifier-name tiebreaker on hit-count ties (PR #55, post-fix live validation)** — first re-fire of the tracing prompt against the post-fix backend revealed a deeper bug: `find_best_qualifier()` iterated `qualifiers.dict` in insertion order and returned the FIRST group at the highest count, so when `embedding`(1)/`metrics`(1)/`tracing`(1) all tied at one hit each, dict insertion order picked `embedding` (semantically wrong) over `tracing` (whose name appears verbatim in the prompt). Fix: when multiple qualifier groups tie on hit count, prefer the one whose **name** appears as a substring of the text. Higher hit count still wins outright (semantic relevance > lexical hit); name-tiebreaker only fires on actual ties; falls back to deterministic insertion order when neither name is in the text. Live verified: same prompt now resolves to `backend: tracing` instead of `backend: embedding`. 7 new unit tests pinning the tiebreaker, the hit-count-dominates-name-match rule, the no-hits zero case, the all-zero-skipped case, and the deterministic-under-permutation guarantee.
- **Code-review SEV-MAJOR + MEDIUM follow-up bundle (PR #55, post 3-parallel-reviewer pass)** — three parallel code-reviewer agents (architecture / tests / brand) produced one SEV-MAJOR and three SEV-MEDIUM findings. All addressed: (1) **domain-transform ordering** in `resolve_post_analyze_state` — `domain_resolver.resolve()` ran BEFORE post-LLM normalization, locking `effective_domain` to the un-normalized LLM string while `domain_raw` got the canonicalized form; downstream consumers (E1 agreement tracking, `Optimization.domain`, strategy-intelligence keys) silently diverged. Fixed by sequencing the post-LLM reconciliation block (normalize + qualifier enrichment) BEFORE `resolver.resolve()`; transforms unchanged, only call order flipped. (2) **Topic-row similarity dropped** — `record_injection_provenance` accepted a `similarity_map` argument but `persist_and_propagate` never passed one, so every topic-row provenance entry landed with `similarity=NULL`. Fixed by deriving the map from per-pattern `InjectedPattern.similarity` when the explicit map is absent — each injected pattern already carries its cluster's similarity. (3) **Cargo-cult lazy imports** — `task_type_classifier`, `domain_detector`, `classification_agreement`, and `pattern_injection.record_injection_provenance` were imported inside try/except inside `resolve_post_analyze_state`/`persist_and_propagate`. None create import cycles; lifted to top-of-file. (4) **`_normalize_llm_domain` qualifier case** — `Backend-OBSERVABILITY` would emit `backend: OBSERVABILITY`, breaking the lower-case lookup contract everywhere else in the pipeline. Now `.lower()`-normalized for parity with `parse_domain()`. (5) **Frontend chip-strip zero-glow regression guard** — the four new chip-strip tests didn't run `assertNoGlowShadow(container)`, so brand-guideline drift on the new path could land silently. Added to the dominant-chip test, which renders the chip-strip path most heavily. Net change: only `pipeline_phases.py` ordering + `pattern_injection.py` sim-map fallback are behavior-affecting; other items are cleanup.
- **Sub-domain label canonicalization — single source of truth (PR #55)** — user-asked-for defensive guard against future Haiku quirks producing `"embedding_health"`-style names instead of canonical kebab-case. New shared helper `normalize_sub_domain_label(raw, max_len=30)` in `app.utils.text_cleanup` replaces the two duplicated inline rules (`labeling.py:265` had `[:20]` + `replace(" ", "-")`, `engine.py:2529` had `[:30]` + same replace). Five hardenings: (1) **underscores → hyphens** so `embedding_health` becomes `embedding-health`; (2) **multiple separators collapsed** so `embedding--health` and `a_ b` become single-hyphen; (3) **leading/trailing hyphens stripped** so `-audit-` becomes `audit`; (4) **word-boundary truncation** when over 30 chars — caught the live cycle-4 regression `pattern instrumentation` (23 char kebab) used to truncate to `pattern-instrumentat` mid-word at the labeling.py limit of 20; (5) **length limit unified at 30** across both stages so vocab group names match what eventually becomes a sub-domain label. Hard-truncation still applies for single oversized words with no hyphens (rare; the prompt instructs Haiku for 1-2 word names). 20 new tests pin: simple/already-canonical/uppercase/space-sep/underscore-sep/mixed-sep/multi-hyphen/whitespace/leading-trailing/empty inputs, the live `pattern-instrumentation` case, 30-char fits, overflow word-boundary cut, single-word hard-truncate, max_len override, idempotence (over uppercase, underscores, and post-truncation), and separator-only-input → empty.
- **Task-type rescue via structural code evidence (PR #55)** — user-reported persistent bug: code-aware prompts like `"Background Task Lifecycle Tracking"` intermittently classified as `creative` by the LLM analyzer. Root cause: the classifier's `creative` signals are deliberately broad (`create:0.5`, `design:0.7`, `concept:0.6`) so prose creativity prompts route correctly — but they collide with code vocabulary like `"create a class"`, `"design the schema"`, `"the lifecycle concept"`. The fix mirrors the existing B2 enrichment-profile rescue: when the FIRST SENTENCE has unambiguous syntactic markers of code (snake_case identifiers like `_spawn_bg_task`, PascalCase classes with structural separators like `EmbeddingService.embed_single`, technical nouns like `asyncio`/`coroutine`), structural evidence beats semantic vibes. Override scope is intentionally narrow — only `creative`→`coding` and `writing`→`coding` transitions; `analysis`/`data` are NOT rescued because they legitimately co-occur with code identifiers (auditing a function, extracting a column). The rescue helper is self-contained on the original-case first sentence (the legacy `has_technical_nouns()` codepath silently lost PascalCase detection by lowercasing input before delegation — that codepath is left intact since other callers depend on it; the rescue uses its own scan). Logs the trigger reason (e.g. `"creative→coding rescue (snake_case identifier '_spawn_bg_task')"`) for trace-walking. 11 new unit tests covering snake_case rescue, PascalCase+dot rescue, bare PascalCase+dot rescue, technical-noun rescue, pure-prose pass-through (NOT rescued), already-coding idempotence, analysis/data NOT rescued (scope guard), empty-prompt edge, and the user-reported pinned regression case.

### Added
- **Source-breakdown chip strip on `SubDomainEmergenceList` (PR #55, observability follow-up)** — operator-asked-for visibility addition triggered by the cycle-3 incident. The qualifier candidate row now exposes the cascade-pipeline contribution as three sibling chips (`RAW:N · INT:N · TFI:N`) below the consistency meter — the dominant source is tinted with its semantic color (cyan/pink/indigo per the existing source-color convention), zero-count chips dim to subtle. Pre-fix, the smoking-gun diagnostic that "all qualifier signal came from intent_label fallback, none from domain_raw" required querying `/api/domains/readiness` directly; now it's at-a-glance on the readiness card. After the post-LLM enrichment fix soaks, the dominant chip will visibly migrate from `INT` to `RAW` as `domain_raw` starts carrying qualifier syntax. Defensively coerces missing/legacy `source_breakdown` to zero-counts so pre-v0.4.5 readiness rows still render. Pinned by 4 new tests covering the post-fix scenario (RAW=N marked dominant), the pre-fix scenario (INT=N dominant), the legacy-row defensive path, and the 3-chip determinism. Also: gap-display tooltip now expands the +/-pts sign convention into plain language ("Consistency exceeds threshold by X pts" / "below threshold by X pts") so the inverted-sign UI (positive = good) is unambiguous.
- **`enrichment` summary on `/api/history` rows (PR #55)** — list endpoint was previously silent on whether each optimization had codebase context / strategy intelligence / pattern injection activated. Caller had to GET each detail individually to know — the failure mode that hid the silent profile demotion above. New `HistorySummaryEnrichment` shape projects `profile`, `codebase_context`, `strategy_intelligence`, `applied_patterns`, `patterns_injected`, `curated_files`, `repo_relevance_score` from the persisted `context_sources` blob. Empty for legacy rows (pre-v0.4.2); populated for current rows. The hidden-demotion case is now visible at-a-glance side-by-side with healthy `code_aware` rows for direct comparison. Robust against malformed `context_sources` shapes (string-instead-of-bool, list-instead-of-dict, missing keys) — silently coerces to `None` for unexpected types so a single bad row never breaks the list endpoint.
- **Multi-sibling sub-domain test coverage (PR #55)** — 3 explicit pinning tests for the contract that multiple sub-domains can coexist as siblings under one parent domain. Live observation: cycle 2 produced an `audit` sub-domain under `backend`. The user asked to confirm the architecture supports additional siblings (`audit` + `embedding` + `concurrency` etc.) — schema-wise it does (partial unique index on `(parent_id, label) WHERE state='domain'`), the discovery loop iterates every qualifier above threshold (no winner-take-all), and `_reevaluate_sub_domains()` evaluates per-sibling so a degraded sibling can dissolve while a healthy sibling survives the same cycle. All three layers now have direct tests.

### Changed
- **Observability separation-of-concerns sweep — `ActivityPanel` (topology terminal) vs `DomainLifecycleTimeline` (Observatory)** — both surfaces consumed `clustersStore.activityEvents` and shared filter/summary logic; a 4-item audit (`docs/observability-separation-audit.md` thread) found drift between the two and one cross-surface side-effect. Each fix lands behind a regression test:
  - **Shared `$lib/utils/activity-filters.ts`** — new module exports `isErrorEvent(e)` (catches `op === 'error'` plus the canonical decision set: `rejected | failed | seed_failed | candidate_rejected`) and `opFamily(op, decision)` (canonical mapping over the 33-op backend vocabulary; returns `null` for pure-infrastructure ops like `phase`/`refit`/`umap` that belong in the terminal feed but NOT the lifecycle timeline). 11 unit tests, including a drift-guard against the 7 historically-dead op names that lived in `Timeline.OP_TO_FAMILY` (`reevaluate`, `dissolve`, `promote`, `demote`, `re_promote`, `retired`, `meta_pattern` — none emitted by the backend).
  - **Shared `$lib/utils/activity-summary.ts`** — extracts the 220-line `keyMetric()` per-op formatter from `ActivityPanel.svelte` into a pure function. Timeline rows now render the same one-liners (Q-deltas, similarity scores, member counts, intent labels, lift, etc.) instead of just `op + decision`. 24 unit tests covering one representative case per backend op family. ActivityPanel.svelte trimmed from 819 → 607 lines (-212).
  - **Timeline `OP_TO_FAMILY` rewritten over the actual backend op vocabulary** — replaced 7 dead entries with the 7 lifecycle ops the backend genuinely emits (`archive`, `candidate`, `emerge`, `extract`, `state_change`, `template_lifecycle`, `recovery`). Pre-fix these 7 fell through to the "uncategorised" bucket and only rendered when every family chip was on; users could not see the Observatory's lifecycle timeline meaningfully filter by family.
  - **`loadActivityForPeriod` moved from `clustersStore` → `observatoryStore.loadTimelineEvents()`** — the prior implementation rewrote `clustersStore.activityEvents` on every period chip click, silently disturbing the ActivityPanel terminal feed in the topology view. Observatory now owns its own `historicalEvents` buffer; the Timeline component derives its render-side view by merging the live SSE ring (`clustersStore.activityEvents`) with the period buffer (`observatoryStore.historicalEvents`) at render time, deduped by `ts|op|decision` and capped at 200 newest-first events. Two independent generation counters (`_fetchGeneration` / `_timelineGeneration`) prevent stale heatmap responses from invalidating fresh timeline responses or vice versa under fast period flicks. Three new tests pin the separation: OS8 (merge dedup), OS9 (error capture), OS10 (race-guard); two new Timeline tests pin the contract: T11 (live + historical merge), T12 (period change does NOT mutate `clustersStore.activityEvents`).
  - **`clustersStore.loadActivityForPeriod` API removal regression guard** — explicit `expect(typeof clustersStore.loadActivityForPeriod).toBe('undefined')` test prevents the API from accidentally re-emerging.
  - Net: +346 lines of shared modules / -212 lines from ActivityPanel. Frontend test count 1477 → 1518 (+41 — 11 filters + 24 summary + 6 store separation [OS8/9/10/11/12/13] + 2 Timeline merge guards [T11/T12] + 1 cluster-store removal guard − 3 retired AP1/2/3). svelte-check 0 errors. No backend changes.

## v0.4.4 — 2026-04-25

### Fixed
- **Live Pattern Intelligence + Taxonomy Observatory — post-merge spec compliance audit (PRs #50 + #51 follow-up)** — sweep of both Tier 1 implementations against their original design specs caught five spec gaps; each is now closed and locked behind a regression test:
  - **`getClusterActivityHistory` API client** never exposed the `since`/`until` range variant the AH1–AH5 backend cycle introduced — the typed wrapper only accepted `date`. Unioned-shape `ActivityHistoryParams` now models the mutex contract (single-day vs range) at the type level so callers can't accidentally pass both. (`frontend/src/lib/api/clusters.ts`)
  - **Timeline period chips were decorative for the Timeline panel.** The spec said the chips should drive a JSONL backfill on top of the SSE-live ring buffer, but the prior implementation bound them only to the Heatmap and locked the deviation behind a regression test (T10) that asserted the fetch was absent. Wiring restored: a new `clustersStore.loadActivityForPeriod(period)` method does a single `since`/`until` range fetch, dedupes against the ring buffer (key = `ts|op|decision`), sorts newest-first, caps at 200 events. The Timeline `$effect` watches `observatoryStore.period` + on first sparse mount, then re-fires on every period change. T10 inverted: now asserts the wired path is called; T10b adds the sparse-mount initial-backfill case.
  - **`DomainReadinessAggregate` cards missing the spec-mandated 6 px chromatic dot.** Spec line 265 explicitly required a "6 px domain-color dot in the header row" so readiness reads consistently with navigator + topology surfaces. Added via `taxonomyColor(domain_label)` per the brand color-resolution discipline (no hardcoded palette). New R8 source-locks both the inline-style dot AND the brand contour grammar (`width: 6px; height: 6px`).
  - **`PatternDensityHeatmap` rows missing hover affordance + tooltip.** Spec line 277: "Hover row: 1 px inset cyan contour + tooltip with the absolute counts + timestamp of the last update." Added `:hover` 1 px inset cyan contour (zero blur, zero spread, brand-canonical), hooked the brand `use:tooltip` action to a new `tooltipFor()` formatter that emits `Domain: X · Clusters: N · Meta-patterns: M (avg S) · Global: G · Cross-cluster injection: P% · Updated: YYYY-MM-DD HH:MM UTC`. H11 source-locks the integration end-to-end (mouseenter → action overlay → text content); H12 source-locks the contour rule.
  - **Activity-history endpoint emitted within-day events oldest-first in BOTH single-day and range modes.** The JSONL append-order is chronological; the spec wants reverse-chronological at every level (across days AND within each day). Backend now reverses the per-day slice in both branches so the contract is uniform. New AH6 (range) + AH7 (single-day) tests assert strict newest-first order. Frontend `loadActivity()` no longer needs the client-side `.reverse()` — backend honours the contract authoritatively.

### Added
- **Taxonomy Observatory Tier 1 — three-panel observability dashboard** — new pinned `OBSERVATORY` tab in the workbench tablist mounts `TaxonomyObservatory.svelte`, a flex-grid shell composing three panels:
  - **`DomainLifecycleTimeline.svelte`** — reverse-chrono activity stream from `clustersStore.activityEvents` + backfill from `GET /api/clusters/activity/history?since=...&until=...`. Per-row 20px height, 60px Geist Mono timestamp column, neon path-color badge (hot=red, warm=yellow, cold=cyan via the new shared `frontend/src/lib/utils/activity-colors.ts` helper that ActivityPanel also consumes). Filter chips for path (3) + op-family (4: domain, cluster, pattern, readiness — 1-to-1 op→family lookup, no cross-family overlap) + errors-only toggle. Click-to-expand reveals the JSON context payload.
  - **`DomainReadinessAggregate.svelte`** — `repeat(auto-fill, minmax(280px, 1fr))` card grid composing the existing `DomainStabilityMeter` + `SubDomainEmergenceList` per domain. Sorted critical → guarded → healthy. Click dispatches `domain:select` CustomEvent with `{domain_id}`; mid-session-dissolution guarded via `readinessStore.byDomain()` null-check.
  - **`PatternDensityHeatmap.svelte`** — read-only data grid (no `role="button"`/tabindex/cursor) backed by the new `GET /api/taxonomy/pattern-density` aggregator. Row backgrounds tinted with `taxonomyColor(domain_label)` opacity-scaled to `meta_pattern_count` (`HEAT_MAX_PCT=22%` ceiling for WCAG-AA contrast on the dimmer domain hues). Loading dims body to 0.5 opacity; error renders 1px neon-red inset contour + retry button.
  - **`TaxonomyObservatory.svelte` shell** — period-selector legend explains the asymmetry (period chips drive Timeline + Heatmap via `observatoryStore`; Readiness is current-state with no period). Period chips live in the Timeline filter-bar, NOT in the shell header.
  - **Backend additions**: extended `GET /api/clusters/activity/history` with `since`/`until` range variant (mutex with `date`, 30-day cap, defaults to today); new `GET /api/taxonomy/pattern-density` endpoint with `Literal["24h","7d","30d"]` Query type → automatic 422 on invalid period; new `backend/app/services/taxonomy_insights.py` aggregator (one row per domain: cluster_count, meta_pattern_count, meta_pattern_avg_score, global_pattern_count via Python-side `source_cluster_ids` containment, cross_cluster_injection_rate from `OptimizationPattern` injected/global_injected events filtered to period). New `backend/app/schemas/taxonomy_insights.py` Pydantic v2 response shapes.
  - **Frontend additions**: `frontend/src/lib/api/observatory.ts` typed client + `frontend/src/lib/stores/observatory.svelte.ts` singleton store with localStorage-persisted `period` (`'24h' | '7d' | '30d'`, defaults `'7d'`), 1s debounced refresh, `_fetchGeneration` race-guard counter mirroring `readinessStore`, `_reset()` test helper.
  - 5 backend AH tests + 5 service PD tests + 3 router PD tests + 7 store OS tests + 10 Timeline T tests + 7 Aggregate R tests + 9 Heatmap H tests (8 contract + 1 zero-rendering regression) + 6 Observatory shell TO tests + 2 Page integration I tests + 3 path-color helper tests = **57 new tests**. ROADMAP entry promoted from "Exploring" to "Tier 1 Shipped".

- **`GET /api/clusters/activity/history` — `since`/`until` range variant** — the activity-history endpoint now accepts a `since`/`until` date pair (each `YYYY-MM-DD`, mutually exclusive with the legacy `date` parameter) and fans out across the per-day JSONL files in newest-first order. `since` alone defaults `until` to today (UTC); `until < since` and ranges exceeding 30 days return 422 with explicit reason text. Empty/missing JSONL files within the range are skipped silently. Pagination semantics (`offset`/`limit`/`has_more`/`total`-as-lower-bound) match the existing single-date branch so consumers don't need a code path fork. Backs the upcoming Taxonomy Observatory range scrubber. 5 router tests (AH1 multi-day fan-out, AH2 missing-day skip, AH3 mutex with `date`, AH4 30-day cap, AH5 `since`-only defaulting).
- **Live Pattern Intelligence Tier 1 — `ContextPanel.svelte`** (ADR-007 first iteration) — replaces the single-banner `PatternSuggestion.svelte` with a persistent sidebar mounted by `EditorGroups.svelte` alongside the prompt editor. Reads `clustersStore.suggestion` and renders matched cluster identity (label + similarity % + match_level + domain dot), meta-patterns checkboxes, and a separately-bordered GLOBAL section for cross-cluster patterns. APPLY button writes the user's selection to `forgeStore.appliedPatternIds` for downstream pipeline injection. Selection persists across applies (panel doesn't auto-dismiss). Handles three transient states: `_matchInFlight` fades the body to 0.5 opacity, `_matchError === 'network'` draws a 1px neon-red inset contour on the header, `_lastMatchedText !== ''` switches the empty-state copy from "waiting for prompt" to "no similar clusters found". Collapse/expand chevron rotates 90° via the brand `--duration-micro` + `--ease-spring` motion, mirroring `CollapsibleSectionHeader.svelte`'s navigator section toggles. Open/closed state persists to `localStorage['synthesis:context_panel_open']`. At < 1400px viewport the panel mounts as a 28px rail by default (user can expand via chevron — their choice persists). Mount-gated to `editorStore.activeTab?.type === 'prompt'` so result/diff/mindmap views stay clean. Hidden during synthesis (`forgeStore.status ∈ {analyzing, optimizing, scoring}`). Full a11y (`role=complementary`, `aria-label`, `aria-expanded`, `aria-controls`) + `prefers-reduced-motion` collapses transitions to 0.01ms. 30 ContextPanel tests + 3 EditorGroups regression tests (I3 + I4 + tab-gating) + 2 PromptEdit regression locks (I1 + I2).
- **Backend `match_level` + `cross_cluster_patterns` keys on `POST /api/clusters/match` response** — `backend/app/routers/clusters.py:717-722` populates two additive keys from `PatternMatch`: `match_level: 'family' | 'cluster'` and `cross_cluster_patterns: MetaPatternItem[]`. Engine already produces both (`backend/app/services/taxonomy/matching.py`); only the router projection was missing them. `ClusterMatchResponse.match` Pydantic shape stays as `dict | None` so the change is fully additive — no schema migration, no consumer breaks. Frontend `ClusterMatch` exported type narrows them to required after store-side defaulting (`cross_cluster_patterns ?? []`, `match_level ?? 'cluster'`). 4 backend tests (B1, B2, B3, B5) + a B4 regression guard locking the pre-existing `cluster.{id,label,domain,member_count}` + `meta_patterns` + `similarity` contract.

### Changed
- **`clustersStore` API: `dismissSuggestion()` removed, `_skippedClusterId` field removed; `applySuggestion()` no longer dismisses** — Tier 1 ContextPanel owns the visibility/selection lifecycle now. `applySuggestion()` returns `{ids, clusterLabel}` and leaves `suggestion` + `suggestionVisible` untouched so the panel stays open for adjustment. Three previously-private bits surfaced as public `$state`: `_matchInFlight`, `_matchError: 'network' | null`, `_lastMatchedText` (lifted from private to public so `ContextPanel` can gate the empty-state copy distinction). All three reset in `_reset()`.

### Removed
- **`frontend/src/lib/components/editor/PatternSuggestion.svelte`** + its test deleted. Replaced by `ContextPanel.svelte` (above). `PromptEdit.svelte` no longer imports or mounts the legacy banner; the `applied-chip` rendering remains for the post-apply UI confirmation.

### Fixed
- **Taxonomy Observatory brand-audit pass — chip density, header layout, heatmap tint** — Plan #5 shipped with three brand-spec violations on the live workbench surface. Each is now both fixed and locked behind a source-asserting regression test so a future style edit cannot silently regress.
  - **`DomainLifecycleTimeline.svelte` filter chips wrapped onto multiple lines** because the long compound labels ("DOMAIN LIFECYCLE", "CLUSTER LIFECYCLE", etc.) competed with `flex-shrink: 1` defaults and the bar lacked `flex-wrap: nowrap` + `overflow-x: auto`. Visible labels shortened to single words ("DOMAIN", "CLUSTER", "PATTERN", "READINESS", "ERRORS"); `aria-label` carries the long form for screen readers, `title` for hover tooltips. `.chip` now declares `white-space: nowrap` + `flex-shrink: 0`; `.filter-bar` declares `flex-wrap: nowrap` + `overflow-x: auto` (scrollbar hidden via `scrollbar-width: none`). Brand-audit tests T11 (chip nowrap+no-shrink), T12 (filter-bar horizontal scroll), T13 (compact labels) lock the contract.
  - **`TaxonomyObservatory.svelte` shell header clipped the legend at narrow widths** because `height: 28px` + `align-items: center` forced single-line layout and the legend got pushed off-screen. Header now uses `min-height: 24px` + `flex-wrap: wrap` + `align-items: baseline` so the legend wraps onto a second line gracefully when horizontal space is tight. Legend gets `flex: 1 1 auto` + `min-width: 0` + sans 10px dim color so it stays brand-subordinated to the Syne 11px title. Brand-audit tests TO8 (header wrap allowed) + TO9 (legend subordination) lock the contract.
  - **`PatternDensityHeatmap.svelte` row backgrounds rendered visually saturated** even at the documented "subtle" 22% ceiling because `color-mix(... transparent)` lets the chromatic shift of a vivid neon (e.g. `#b44aff` violet) read sharply against the near-black page background. Two combined fixes: (1) drop `HEAT_MAX_PCT` 22 → 14 so even the brightest row stays a quiet tint, (2) mix INTO `var(--color-bg-card)` instead of `transparent` so the row composes as a tinted card surface (brand hierarchy tier) rather than a translucent overlay. Brand-audit tests H9 (mix target = bg-card) + H10 (ceiling ≤ 14%) lock the contract.

## v0.4.3 — 2026-04-24

### Added
- **`POST /api/optimizations/delete` bulk REST endpoint** — `backend/app/routers/history.py` now exposes the bulk delete primitive via an HTTP surface. Body: `{ids: list[str], reason?: str}` with `min_length=1`, `max_length=100` Pydantic constraints; rate-limited `10/minute` per IP via the project's `Depends(RateLimit(lambda: "10/minute"))` pattern. Response envelope `DeleteOptimizationsResponse` mirrors the service's `DeleteOptimizationsResult` with the JSON-safe list form plus a new `requested: int` field so the UI can diff `requested - deleted` and report partial matches ("X of Y deleted, Z were already gone") without a second call. The existing `DELETE /api/optimizations/{id}` response gains the same `requested: int` field (always `1`) for envelope isomorphism — additive, backwards-compatible. No service-layer changes: `OptimizationService.delete_optimizations` already emits one `optimization_deleted` event per deleted row + one aggregated `taxonomy_changed` event per bulk call. 7 pytest coverage points: happy path, partial match, empty ids (422), oversized ids (422), rate-limit (429), per-row event emission count, single aggregated event emission.
- **History delete UX — row-level grace window + opt-in bulk mode** — `frontend/src/lib/components/layout/HistoryPanel.svelte` gains three destructive-action affordances. **Row-level single delete**: hover-revealed × icon (neon-red at 0.6α idle, 1.0α focus, Tier-Small contour on button hover) opens a 5-second `UndoToast`; the `deleteOptimization` API call is deferred until the timer expires — clicking Undo cancels the commit entirely (no round-trip). Row enters a `pending-delete` state (gray + strikethrough) during the grace window. Re-entry guard prevents double-commit on rapid clicks. 404 response surfaces "Already deleted elsewhere.", 5xx surfaces "Delete failed." **Opt-in multi-select mode**: `[Select]` toggle in the panel header slides in cyan-when-checked checkboxes; a 28 px selection toolbar appears when `selectedIds.size >= 1` with a count + Cancel + `Delete N` button. **Bulk confirmation via `DestructiveConfirmModal`**: type-to-confirm gate (the literal `DELETE`, case-sensitive) gates the bulk call; side-effect hint ("N clusters will rebalance.") computed client-side from the selected rows' `cluster_id`s; partial-match response surfaces as an info toast. 10 Vitest integration tests covering hover reveal, grace-window commit/undo, SSE surgical removal, error branches, re-entry guard, multi-select toggle, selection toolbar, and bulk confirmation flow.
- **Reusable destructive-action primitives (brand foundation)** — Three new primitives establish the destructive-action visual language for this codebase so future surfaces (unlink repo, retire template, archive cluster, refinement-branch delete) plug in with zero bespoke styling:
  - **`frontend/src/lib/stores/toasts.svelte.ts`** — `toastsStore` singleton. Class-based Svelte 5 runes store with `push` / `dismiss` / `undo` / `pause` / `resume`. The `commit?: () => Promise<void>` hook is what enables the pre-commit grace window: the API call fires on timer expiry only if the user hasn't clicked Undo first. Stack capped at 3 concurrent; oldest ages out. 6 Vitest tests.
  - **`frontend/src/lib/components/shared/UndoToast.svelte`** — Glass-panel (glass morphism at `var(--color-bg-glass)` + `backdrop-filter: blur(8px)`) toast with 1 px neon-red progress bar (linear easing for perceptually uniform countdown), Geist Mono tabular countdown numeric, `navSlide`-preset slide-in, pause-on-hover, `online`/`offline` listener pause/resume. 5 Vitest tests.
  - **`frontend/src/lib/components/shared/DestructiveConfirmModal.svelte`** — Glass-panel modal shell. Syne `text-[11px]` uppercase letter-spacing 0.1em title. Geist Mono `text-[11px]` type-to-confirm input (case-sensitive literal, default `DELETE`). Focus lands on the input on open; Esc cancels; Enter in input fires Confirm when gate passes. Error retry path keeps the modal open and preserves the typed literal so the user can re-click Confirm without re-typing. 7 Vitest tests.
  - All three primitives respect the brand zero-effects directive (no `box-shadow` with blur/spread, no `text-shadow`, no `filter: drop-shadow()`, no pulse animations) and `@media (prefers-reduced-motion: reduce)` collapses transitions to 0.01 ms.
- **`frontend/src/lib/api/optimizations.ts` typed delete client** — `deleteOptimizations(ids, reason?)` + single-id shim `deleteOptimization(id, reason?)` always route through the bulk endpoint so the UI has a single codepath. Preflight validation mirrors backend Pydantic constraints (1 ≤ `ids.length` ≤ 100) with a SYNC comment pointing to the backend source of truth, so the preflight error message can't silently drift from the server response. Reuses the existing `ApiError` class from `frontend/src/lib/api/client.ts`. 5 Vitest tests (happy path, single-id shim, 404 → ApiError, oversized preflight, empty preflight).
- **HistoryPanel keyboard shortcuts + modifier-aware click semantics** — full file-manager-style selection grammar on the History panel: **Ctrl/Cmd+Click** toggles a row into the selection, auto-seeding the currently-active row on the first modifier-click (single click on "row A" → Ctrl+click on "row B" selects BOTH, not just B — prevents the two-click-to-activate quirk); **Shift+Click** extends from the last-selected row through the clicked row (contiguous range selection); **Ctrl+A** selects all rows when the panel has focus; **Esc** exits select mode and clears the selection; **Delete/Backspace** opens the bulk-confirm modal when ≥1 row is selected; **Arrow Up/Down** moves focus between rows with wrap. All shortcuts respect the `pending-delete` grace-window state (selected rows that are mid-grace-window are filtered out of bulk commits so the user can't double-delete during the 5s undo window). Modifier-click auto-seed matches macOS Finder / Windows Explorer / VS Code conventions exactly.
- **`e2e_test_workflow.py` delete-flow smoke pass** — the existing internal-tier e2e harness is extended with a create → bulk-delete → history-confirmation pass: `POST /api/optimize` creates an optimization, `POST /api/optimizations/delete` removes it, `GET /api/history` confirms it's gone. Response envelope (`deleted, requested, affected_cluster_ids, affected_project_ids`) printed for easy spot-check. Complements `e2e_sampling_workflow.py` (sampling-tier).

### Changed
- **Frontend brand-guidelines strict audit — zero violations** — one-shot sweep of every `.svelte`, `.css`, and `app.css` file against the brand spec (zero-effects directive, Tier-Small contour grammar, flat-edges default, zero-glow rule, accent-at-30%-alpha focus rings, neon-tube-model borders). Removed two stray `text-shadow` rules on sidebar hover states that slipped through the earlier audit, consolidated three remaining `border-radius: 4px` surfaces to the canonical `0`, replaced one `box-shadow: 0 2px 8px rgba(...)` with a `1px` inset contour on the Tier-Large card hover, and purged `--glow-*` CSS variable references from legacy comments. All new delete-UX primitives (`UndoToast`, `DestructiveConfirmModal`, `toastsStore`, row × affordance) were built against the audited spec from the start.
- **Delete-UX design-review polish** — single-pass sweep on the three destructive-action primitives catching items missed during TDD: `UndoToast` progress-bar `transform` is now `scaleX(progressScale)` with `transform-origin: left center` + `will-change: transform` (RAF-driven compositor-only repaints, previously was re-rendering width each frame); `DestructiveConfirmModal` error banner restricted to the modal width via `margin: 0 6px` (was spanning full scrim); confirm-literal input Enter key now fires Confirm only when the gate passes (was firing submit regardless of typed value); focus returns to the opener on cancel (implicit via Svelte unmount, now explicit via `requestAnimationFrame(() => opener?.focus())`).
- **Delete-UX accessibility + code-doc pass** — review of the delete surface for AT + maintainability: `aria-live="polite"` on `UndoToast` wrapper so the toast's message + countdown are announced on mount (countdown itself is `aria-hidden="true"` so each second isn't re-announced); `role="dialog"` + `aria-modal="true"` + `aria-labelledby="confirm-modal-title"` on `DestructiveConfirmModal`; `onkeydown` handlers trap Esc at the document level with a `committing` guard (can't Esc-cancel mid-commit); module-level docstrings on `optimizations.ts` + `toasts.svelte.ts` + `UndoToast.svelte` + `DestructiveConfirmModal.svelte` explain the contract, why the split-endpoint pattern exists (graceful fallback for pre-v0.4.3 backends), and why the grace-window hook lives in the store (keeps the commit callback co-located with its cancellation path).

### Fixed
- **`optimization_deleted` SSE event now consumed by the frontend** — the event has shipped since v0.4.2 but no UI handler existed, so MCP-tool-initiated or external deletes left stale rows in History until manual refresh. The root dispatcher in `frontend/src/routes/app/+page.svelte` now bridges `optimization_deleted` (backend snake_case) → `CustomEvent('optimization-deleted')` (frontend DOM kebab-case), and `HistoryPanel` subscribes + removes the matching row surgically (no full re-fetch). Fallback 2-second client-side timeout covers SSE stream gaps so zombie rows never persist even under reconnect. Unlike the consolidated `optimization-event` (which signals "history changed, re-fetch"), deletion gets a dedicated event because consumers need the per-row `id` for surgical removal.
- **Delete endpoints now hit the backend via `BASE_URL`** — `frontend/src/lib/api/optimizations.ts` routes both the per-id `DELETE /api/optimizations/{id}` and the bulk `POST /api/optimizations/delete` through the shared `apiFetch` helper so `BASE_URL` (dev → `http://localhost:8000/api`, prod → relative `/api`) is applied uniformly. Previously the delete surface wrote `fetch('/api/...')` literals, which in dev bypassed the `VITE_API_URL` target and hit the frontend port (5199) — silently 404ed with no Vite proxy configured. Live-observed after the first bulk delete against a dev instance; now immune to port drift.
- **Robust bulk-to-single fallback** — `HistoryPanel` bulk delete catches 404 on `POST /api/optimizations/delete` (backend hasn't been restarted with the v0.4.3 router registered yet) and falls back to parallel per-id `DELETE /api/optimizations/{id}` calls. The fallback path only engages on bulk 404 — the per-id endpoint has shipped since v0.4.2, so the UX degrades gracefully instead of failing opaquely during a rolling deploy.
- **Visual-inspection polish from live renders** — six small frontend regressions caught by comparing live screenshots against the brand spec: (1) the row-select checkbox no longer stacks as a "header" above the two-line row (it was flex-direction column laying out above the row content) — now absolutely positioned with `padding-left: 30px` on the row body in select mode; (2) the × delete button no longer overlaps the row timestamp — moved into `.history-meta` as the last flex child with `margin-left: auto`; (3) the × is now gated on `!selectMode` so it doesn't render beside a checkbox; (4) checkbox uses cyan-when-checked neon at 0.6α idle / 1.0α focus matching brand Tier-Small contour; (5) pending-delete rows gain a `text-decoration: line-through` + text dim during the grace window for clearer state-awareness; (6) focus outline on the × button uses the standard brand 1px cyan at 0.3α with 2px offset.
- **`.toast-stack` z-index raised above modal layer** — initially placed at `z-index: 800`, which is below `DestructiveConfirmModal`'s scrim (900) and panel (901). A single-row delete toast opening during the grace window could become unreachable if the user opened a bulk-confirm modal over it. Raised to `z-index: 1100` so the pre-commit toast + its Undo button stay interactive even when a modal is stacked above (still below the 9999 tier reserved for CommandPalette / SeedModal).
- **Row delete button is keyboard-accessible** — the hover-reveal `× ` affordance initially used `tabindex="-1"`, making it unreachable via tab. Changed to `tabindex="0"`; the existing `.row-item:focus-within .row-delete-btn { opacity: 0.6 }` rule reveals the button when the parent row receives keyboard focus, and the button's own `:focus-visible` outline provides the focus indicator. Keyboard users can now Tab → row → Tab → × → Enter to delete.
- **`role="textbox"` removed from the read-only prompt span** — pre-existing mis-ARIA on the inline-rename affordance; the span was display-only (double-click triggers rename), not an editable text input. The assertive `role` was a screen-reader lie. Noticed while wiring the confirm modal (whose legitimate textbox input collided with the false one during a `getByRole('textbox')` test query). Replaced with `<!-- svelte-ignore a11y_no_static_element_interactions -->` since the actual semantic is a deferred-for-refactor double-click trigger.
- **`task_type_signal_extractor` degrades gracefully when `task_type_telemetry` table is missing** — live regression on instances that hadn't run `alembic upgrade head` after the `2f3b0645e24d` migration landed: warm Phase 4.75 crashed with `OperationalError: no such table: task_type_telemetry` and halted the whole warm-path cycle, which in turn left `member_count` stale on domain nodes after deletes (the cross-process dirty-set bridge fires before warm Phase 0 runs, but the warm-path task aborted before Phase 0). Now the telemetry INSERT is wrapped in `try/except OperationalError` — the classifier still records its comparison result in-process (for `ClassificationAgreement` aggregation), just doesn't persist to the DB when the table is missing. Fresh DBs that bootstrap via `Base.metadata.create_all()` continue to record telemetry normally; unmigrated DBs get a warn-log once per cycle instead of a crash.
- **Test isolation helpers — public `reset_rate_limit_storage()` + shared `drain_events_nonblocking()`** — `backend/app/dependencies/rate_limit.py` gains a public `reset_rate_limit_storage()` that clears the in-memory SlowAPI rate-limit bucket between tests (previously a private helper accessed via module spelunking across multiple test files). `backend/tests/conftest.py` grows `drain_events_nonblocking(queue)` — drains an `asyncio.Queue` of subscribed `event_bus` deliveries via `get_nowait()` until `QueueEmpty`, the deterministic way to collect everything emitted during a unit-test arrangement (the public async-generator API races under test timing). Both helpers were inlined across delete/bulk-delete router + service tests during the v0.4.3 work; surfacing them as public utilities removes the duplication and makes the bulk-delete event-emission test deterministic under parallel `-n auto` runs.

## v0.4.2 — 2026-04-23

### Added
- **MCP sampling architecture unification + Hybrid Phase Routing** — `MCPSamplingProvider` now encapsulates the IDE LLM sampling protocol as a first-class `LLMProvider`; the 1,700-line redundant sampling pipeline collapses to a thin re-export layer while sampling is routed natively through the primary `PipelineOrchestrator`. Hybrid Execution Routing: fast phases (analyze, score, suggest) stay on the internal provider while optimize routes through the IDE LLM, avoiding the 5-round-trip penalty of the old sampling-only path. MCP transport timeouts + errors now map to the `ProviderError` class so Tenacity exponential backoff retries kick in. Patched `StreamableHTTPServerTransport` to correctly extract the TS SDK `sessionId` from query parameters during handshake.
- **`TaskTypeTelemetry` model + migration `2f3b0645e24d_add_task_type_telemetry.py`** — records heuristic vs LLM classification events (`raw_prompt`, `task_type`, `domain`, `source`) for drift analysis and A4 confidence-gated fallback tuning. Feeds future classifier calibration without needing to re-run the pipeline.
- **Inspector analyzer telemetry rendering (UI2)** — `ForgeArtifact.svelte` ENRICHMENT panel now surfaces the three analyzer fields that previously shipped in `enrichment_meta` with no UI: **signal source** tag (bootstrap vs dynamic, A4 — legacy `static` read-compat collapses to `bootstrap`), **TASK-TYPE SCORES** detail block (6-class distribution with winner highlighted, guarded by `nonZero.length > 0` so all-zero vectors don't render an orphan heading), and **CONTEXT INJECTION** detail block (auto-injection counts with an `explicit` badge when the caller supplied `applied_pattern_ids`). `build_pipeline_result()` also propagates `inputs.repo_full_name` into `PipelineResult.repo_full_name` so the SSE `optimization_complete` event mirrors what gets persisted (previously the SSE stream always reported `null`).
- **`enrichment_meta.injection_stats` uniform emission (UI1) + AA1 auto-bind for session-less callers** — `ContextEnrichmentService.enrich()` now emits `injection_stats` for every tier with a single schema (`patterns_injected`, `injection_clusters`, `has_explicit_patterns`); internal + sampling pipelines overwrite the zero-count placeholder with real auto-injection counts after `auto_inject_patterns()` runs. New `project_service.resolve_effective_repo()` resolves `repo_full_name` via explicit → session-cookie → most-recently-linked cascade; `POST /api/optimize` + `POST /api/optimize/passthrough` use the helper so curl / API callers without session cookies auto-bind to the live `LinkedRepo`'s project instead of falling through to Legacy.
- **Explicit DOMAIN SIGNALS + RETRIEVAL section headings + CLI-family classifier coverage (A8)** — retrieval and domain-signal detail rows in the Inspector now wrap in `.enrichment-detail` with explicit headings for visual parity with TASK-TYPE SCORES / CONTEXT INJECTION / DIVERGENCES; new `.enrichment-diagnostics--nested` variant drops the double top-border that would stack against the heading rule. Classifier side: `cli`/`daemon`/`binary` added to `_TECHNICAL_NOUNS` (A2 verb+noun disambiguation) and as coding-signal keywords (coding_score > 0 gate) at moderate weights (0.7/0.7/0.5) so creative writing with incidental mentions ("a binary decision") is unaffected.
- **`e2e_sampling_workflow.py` end-to-end sampling harness** — new repo-root script exercises the sampling tier against a live MCP session (bridge extension + VS Code) to verify the Hybrid Phase Routing path end-to-end. Complements the existing `e2e_test_workflow.py` internal-tier harness.
- **`DELETE /api/optimizations/{id}` REST endpoint + `synthesis_delete` MCP tool (audit bugs #3 + #4)** — wire the long-existing `OptimizationService.delete_optimizations()` primitive into both the REST API (`backend/app/routers/history.py`) and the MCP surface (`backend/app/tools/delete.py`, registered in `mcp_server.py`). Both entry points translate the service's silent `deleted=0` on unknown id into a proper 404 / `ValueError` so typos can't masquerade as successful no-ops. Response envelope mirrors `DeleteOptimizationsResult` (`deleted`, `affected_cluster_ids`, `affected_project_ids`) so clients can surface "cluster X will rebalance" hints after a delete. Cascade, event emission, and dirty-marking behaviour are owned by the service and unchanged.
- **`POST /api/taxonomy/reset` admin recovery endpoint (I-0)** — one-command cleanup for structural debris left over after a bulk delete (archived clusters with `member_count=0`, orphan project nodes, stale signal caches, empty embedding index). Force-prunes archived zero-member clusters regardless of the 24h grace floor that normal warm Phase 0 respects (admin-triggered reset is by definition deliberate), then delegates to `run_warm_path` synchronously for the full reconciliation sweep (member_count, embedding index rebuild, domain signal refresh, meta-pattern orphan cleanup) — bypasses the 30s debounce. Idempotent.
- **`taxonomy_changed` SSE publish on bulk delete (I-0)** — `OptimizationService.delete_optimizations()` emits a single `taxonomy_changed` event after a successful bulk delete with `{reason, trigger: "bulk_delete", affected_clusters, affected_projects}`. In-process `engine.mark_dirty()` is retained as the fast path; the SSE event is the cross-process safety net for MCP/CLI contexts where the engine singleton isn't resident.
- **Inspector per-layer enrichment skip reason (I-9)** — `ForgeArtifact.svelte` renders a right-aligned reason tag next to each gray enrichment layer (e.g. "skipped — cold start profile", "deferred to pipeline" under internal/sampling tiers). Consumes `enrichment_meta.profile_skipped_layers` + `patterns_deferred_to_pipeline`. Makes "by design" skips visually distinguishable from bugs.
- **`scripts/reset_taxonomy.py`** — one-shot Python recovery script that deletes stale archived zero-member clusters + orphan meta-patterns directly against the SQLite DB, for environments where the API endpoint isn't available (migration windows, CLI-only recovery).
- **Tree integrity repair SSE events (I-8)** — `_repair_tree_violations` emits one `tree_integrity_repair` event per repaired row with `{violation_type, action, label}` so the ActivityPanel surfaces what was wrong instead of silently counting repairs. Enumerated violation types: `weak_persistence`, `orphan_cluster`, `domain_mismatch`, `self_reference`, `non_domain_parent`, `archived_with_usage`.
- **`OptimizationService.delete_optimizations(ids, *, reason)` — bulk delete primitive with DB cascade** — new service method in `backend/app/services/optimization_service.py` that deletes optimizations by id and relies on DB-level `ondelete="CASCADE"` (migration `a2f6d8e31b09`) to remove `Feedback`, `OptimizationPattern`, `RefinementBranch`, and `RefinementTurn` dependents atomically. `PromptTemplate.source_optimization_id` is auto-nulled by its `ondelete="SET NULL"` rule — templates are immutable forks that outlive their source. Emits one `optimization_deleted` event per deleted row with payload `{id, cluster_id, project_id, reason}` for SSE clients. Returns `DeleteOptimizationsResult(deleted, affected_cluster_ids, affected_project_ids)` so callers can publish `taxonomy_changed` and kick warm Phase 0 reconciliation immediately instead of waiting for the 30s debounce. Closes the last gap between "Optimization is the primitive" and "every downstream aggregate reconciles from live rows": a future delete endpoint is now plumbing, not architecture.
- **Migration `a2f6d8e31b09_cascade_optimization_fks.py` — `ondelete="CASCADE"` on four FKs referencing `optimizations.id`** — `feedbacks.optimization_id`, `optimization_patterns.optimization_id`, `refinement_branches.optimization_id`, `refinement_turns.optimization_id` now cascade at the storage layer. Previously every deletion path had to hand-roll cascade ordering (see `services/gc.py::_gc_failed_optimizations`). Uses `batch_alter_table(recreate="always")` for SQLite compatibility, with an idempotency guard that skips tables already on CASCADE. Symmetric `downgrade()` restores plain FKs. Left unchanged: `prompt_templates.source_optimization_id` + `source_cluster_id` (both `SET NULL`) — templates are immutable forks. ORM layer in `app/models.py` updated on the four FK columns to match.
- **Migration `b3a7e9f4c2d1_drop_duplicate_optimization_fks.py` — remove duplicate unnamed FKs left by `a2f6d8e31b09`** — `a2f6d8e31b09` reflected FK names from the DB and conditionally dropped the old FK before creating the new CASCADE one. Pre-migration the four target FKs had `name=None` (unnamed), so the drop was a silent no-op and the four affected tables ended up carrying BOTH the old unnamed `NO ACTION` FK and the new named `CASCADE` FK side-by-side in `sqlite_master`. Cascade still worked correctly (SQLite honours CASCADE whenever any matching FK declares it), but the duplicate confused `sqlite_master` dumps and risked misleading inspectors that pick the first match. The fix does a raw SQLite table-rebuild dance (PRAGMA foreign_keys=OFF → CREATE `<table>__rebuild_tmp` from SQLAlchemy-reflected schema [which dedupes FKs by `(column, target)` and keeps the named CASCADE one] → INSERT rows → DROP old → RENAME new → replay captured index DDL → PRAGMA foreign_keys=ON). Idempotent via `_opt_fk_count()` PRAGMA probe. Downgrade is deliberately a no-op — restoring the duplicate would be hand-crafted SQL with no operational value; downgrading past `a2f6d8e31b09` is the supported rollback path.
- **`optimization_deleted` event** — registered in the root `CLAUDE.md` event bus type list. Payload: `{id, cluster_id, project_id, reason}`. Consumed by SSE subscribers (HistoryPanel live updates) and, indirectly, warm Phase 0 via a follow-up `taxonomy_changed` publish from the caller.

### Changed
- **Warm Phase 0 clears stale `learned_phase_weights` on empty clusters** — `backend/app/services/taxonomy/warm_phases.py::phase_reconcile` now pops `cluster_metadata["learned_phase_weights"]` from any cluster reconciled to `member_count == 0`. Previously the learned profile lingered on the node until the 24h archival window closed, risking "phantom learning" if the cluster id was reused or later reacquired members. Other metadata keys are preserved.
- **Threaded provider through enrichment + tool handlers** — `HeuristicAnalyzer.analyze()` and `ContextEnrichmentService.enrich()` now accept a `provider` kwarg at every call site (`optimize` router, `refinement` router, `batch_pipeline`, `pipeline_phases`, `refinement_service`, `tools/optimize`, `tools/prepare`, `tools/refine`, `tools/save_result`). The A4 confidence-gated Haiku fallback can now resolve the correct provider instance without a global lookup — eliminates a class of race conditions in sampling and passthrough tiers where `app.state.routing` was still settling.
- **Negation-aware weakness detection + signal-source accuracy (`weakness_detector` + `task_type_classifier` refactor)** — weakness keywords preceded by negators (`not`, `no`, `without`, `avoid`, `never`, `don't`, `doesn't`, `won't`, `shouldn't`, `cannot`, `can't`) no longer count as positive signals (`_is_negated()` helper); structured prompts under 50 words with high structural density skip the `underspecified` flag via a new `_compute_structural_density()` helper. `task_type_has_dynamic_signals()` is now driven by a `_TASK_TYPE_EXTRACTED` set that records which task types crossed `MIN_SAMPLES` on the current warm extraction pass, so the `task_type_signal_source` field in the analyzer payload distinguishes `bootstrap` (static defaults) from `dynamic` (live TF-IDF) honestly instead of reporting `dynamic` whenever non-compound singles were present.
- **`task_type_signal_source` renamed `static` → `bootstrap` (A4)** — the warm Phase 4.75 and `main.py` startup paths now pass `extracted_task_types=set(tt_signals.keys())` into `set_task_type_signals()`, and the MCP process's cache-only load correctly reports `bootstrap`. Legacy `static` value still accepted as a read-compat synonym for one release cycle.
- **Analyze phase effort clamped to `high` ceiling (A3)** — new `ANALYZE_EFFORT_CEILING='high'` in `pipeline_constants.py` + `clamp_analyze_effort()` helper applied at all three analyze call sites (`pipeline.py`, `batch_pipeline.py`, `refinement_service.py`). Observed live: 212s analyze on a 361-char prompt at `sonnet-4-6` `effort=max` — thinking tokens on a 50-output-token classification task. Deep thinking adds no measurable quality to structured `AnalysisResult` JSON, so `max`/`xhigh` now downshift to `high`; `medium`/`low` pass through; `None` / unknown values default to `low`. Ceiling does NOT apply to optimize/score — they genuinely benefit from deep thinking. Expected impact: analyze phase drops from 200+s to 30–60s at `effort=high`.
- **`auto → strategy` routed by `intent_label` keywords (A2)** — `resolve_effective_strategy` step 6 (auto → task-type map) was blind to `intent_label`; audit/debug/extract/story prompts collapsed into the generic task-type default. New intent override (step 5b) inspects `intent_label` for strong keyword hits before falling through: `chain-of-thought` (audit, debug, diagnose, review, compare, investigate, trace, root cause), `structured-output` (extract, classify, list, schema, tabulate, enumerate), `role-playing` (story, poem, narrative, character, dialogue, persona). Unavailable / blocked picks fall through, intent-less or ambiguous intents ("improve", "make") also fall through — under-triggering preferred to misrouting. Threaded `intent_label` through all four call sites.
- **`enrichment_meta.domain_signals` shape changed — `{resolved, score, runner_up}`** — previously wrote `analysis.domain_scores` verbatim (the candidate-score table from `DomainSignalLoader.score()`), which misled the UI into rendering a runner-up candidate as if it were the resolved domain. New shape names the winner explicitly, with `runner_up` populated only when within 0.15 of the winner AND not outscoring it AND non-zero. Frontend `ForgeArtifact` renderer detects shape via `typeof raw.resolved` (new shape: winner + optional runner_up; legacy `{label: score}` dict stays readable until old rows age out). Qualifier suffixes ("backend: auth") stripped to match the score table.
- **`reconcile_domain_signals()` + broadened intent override gate (A1+A2 follow-up)** — the heuristic `DomainSignalLoader.classify()` demotes winners below the 1.0 promotion threshold to `"general"`; the LLM + `DomainResolver` then upgrade to the specific label, but `enrichment_meta.domain_signals` was frozen at enrichment time and contradicted the final `optimization.domain`. New `reconcile_domain_signals(meta, effective_domain)` rebuilds the block against the resolved domain after `pipeline.py` / `sampling_pipeline.py` / `batch_pipeline.py` finalize it; `heuristic_domain_scores` is preserved as evidence. The step 5b intent override now also fires when the current effective strategy equals `_TASK_TYPE_DEFAULTS[task_type]` (not only the literal `"auto"`), so audit/debug prompts where the LLM echoes the generic default no longer silently ignore the intent signal. LLM picks that diverge from the default (e.g. `few-shot`, `role-playing` on a non-creative task_type) are still respected as deliberate choices.
- **`/api/health` surfaces `taxonomy_index_size` + `avg_vocab_quality` at top level** — Plan I-1 wired boot logging but the corresponding health fields were stuck at `None` in production. Both are now pulled off `app.state.taxonomy_engine`: `embedding_index.size` → `taxonomy_index_size`; `_vocab_quality_scores` rolling mean → `avg_vocab_quality` (mirrored into `qualifier_vocab.avg_vocab_quality` for back-compat). Null when the engine isn't wired (test contexts without a live engine) or when the vocab window is empty.
- **`Q_system` / `Q_health` return `None` with fewer than 2 active clusters (A5)** — single-node taxonomies previously reported `Q: 1.00 Just getting started` in the UI because coherence + separation degenerate to perfect scores on a trivial graph. `compute_q_system` / `compute_q_health` now return `None` below 2 active non-structural clusters; Q-gates treat `None → defined` as growth (accept), `defined → None` as destruction (reject), and `None → None` as no-progress (reject). `TaxonomySnapshot.q_system` is NOT NULL at the DB layer, so `create_snapshot` coerces `None → 0.0` for persistence; `get_stats()` recomputes from live cluster counts and returns `q_system=null` so `/api/clusters/stats` is honest about the degenerate case. StatusBar + TaxonomyHealthPanel were already null-guarded.

### Fixed
- **Strategy intelligence fallback now honours the enrichment profile (A9)** — `pipeline.py` + `sampling_pipeline.py` + `tools/optimize.py` thread the resolved `enrichment_meta.enrichment_profile` into a new `should_run_strategy_intelligence_fallback()` gate in `pipeline_constants.py`. Previously the fallback re-fetch re-populated `strategy_intelligence` inside the pipeline even when enrichment had explicitly skipped it under the `cold_start` profile, so `optimize_inject` logged `strategy_intel=82` on runs that had reported `strategy_intel=none` at enrichment. The gate centralises three conditions (feature enabled, not already populated, profile allows SI) and is covered by 8 unit tests across cold-start/knowledge-work/code-aware/disabled/missing-profile combinations.
- **Taxonomy embedding index rebuilds on boot even when sub-systems are partially initialized (I-1)** — startup path now warm-loads the embedding + qualifier + transformation + optimized indices from all eligible active clusters so the very first optimization after restart has a non-empty index to search. Previously a boot race could leave the index empty, causing `auto_inject_patterns()` to log "Taxonomy embedding index empty" and return zero patterns despite eligible data being present.
- **GC hardens archived-zero-member pruning for rows with NULL `archived_at` (I-2)** — `warm_phases::phase_reconcile` now matches archived rows missing the `archived_at` timestamp via `updated_at < NOW() - 24h` fallback. Prevents the specific debris we observed (archived `backend`/`frontend` nodes with NULL `archived_at` that survived GC indefinitely because the `> 24h` clause short-circuited on the NULL).
- **`DomainSignalLoader` excludes archived nodes when counting missing signals (I-3)** — the "N non-general domain nodes without `signal_keywords`" warning now ignores archived nodes + bootstrap-covered labels, so the classifier's actual vocabulary state is what the log reflects. Prevents a cosmetic warning from firing forever while GC is catching up on archived-row cleanup.
- **Curated retrieval escape via B0 cosine (I-4)** — `_should_skip_curated()` now accepts `repo_relevance_score` and lets analysis/system prompts through to curated retrieval when the B0 repo-relevance gate already passed (≥0.15 floor), not just when the prompt hits the 19-word `_CODE_ESCAPE_KEYWORDS` allowlist. The embedding similarity is a better "is this about code?" signal than a keyword list. Writing/creative/general task types still skip as before.
- **Faithfulness heuristic normalization for expansion strategies (I-5)** — `heuristic_faithfulness()` in `backend/app/services/heuristic_scorer.py` now uses an asymmetrical log-length projection: `projection = cosine(original, optimized) * log(max(l1, l2)) / log(l1)`, with both lengths floored at 40 chars to prevent log-underflow on micro-prompts (the floor also dampens the boost for sub-40-char originals) and projection capped at 1.0. Expansions — where the optimizer adds framing / reasoning scaffolding — recover their cosine penalty organically because the log-ratio boost compensates for the length-driven similarity drop; contractions (`l2 ≤ l1`) fall through to raw cosine because `max` collapses to `l1`, preserving penalties for dropped constraints. A piecewise score map projects `[0,1] → [1,10]` with a 9–10 band at projection ≥ 0.85, so faithful expansions no longer systematically under-count. Supersedes the strategy-kwarg + `EXPANSION_STRATEGIES` dampener approach originally scoped under I-5: the projection reaches the same outcome without having to thread a `strategy_used` arg through `score_prompt()` / `pipeline_phases` / `sampling_pipeline` / `batch_pipeline` / `refinement_service`.
- **Cold-start profile is now signal-aware (I-6)** — `select_enrichment_profile()` accepts `meta_pattern_count` and only returns `cold_start` when *both* `optimization_count < 10` AND `meta_pattern_count < 5`. Previously the first 9 optimizations after a fresh DB skipped strategy intelligence + applied patterns even if prior seeding had already produced usable meta-patterns. Callers query the count alongside optimization count.
- **`strategy_intel` observability log parity (I-7)** — `pipeline_phases.build_optimize_context` + `sampling_pipeline` now use a shared `_content_len()` helper that returns 0 for None / empty / whitespace-only strings, so the enrichment `strategy_intel=none` log and the downstream `optimize_inject strategy_intel=N` log agree. Prior inconsistency (enrichment=none, optimize_inject=92) came from a whitespace-only placeholder leaking into the second count.
- **`synthesis_analyze` is now read-only — no DB persistence, no cluster assignment** — `backend/app/tools/analyze.py` and `backend/app/services/sampling/analyze.py` no longer insert `Optimization` rows or call `map_domain()`. The tool is a pure diagnostic that returns the analysis result to the caller; persistence requires an explicit `synthesis_optimize` call. Fixes History vs Clusters inconsistency where repeated analyze-only invocations accumulated `status='analyzed'` rows that inflated `PromptCluster.member_count` (visible in Clusters navigation) while being hidden from History (frontend filters to `status='completed'`). `AnalyzeOutput.optimization_id` is now always `None` with a docstring explaining the contract change.
- **Phase 0 reconciliation defensive guard — `status='completed'` filter** — `backend/app/services/taxonomy/warm_phases.py::phase_reconcile` now counts only `status='completed'` rows toward `member_count`. Diagnostic/transient rows (`analyzed`, `failed`) never inflate cluster membership, preventing recurrence of the History vs Clusters drift via any future code path.
- **One-shot cleanup of 7 stale `status='analyzed'` rows** — direct SQL deletion of orphan analyze-only rows + their `optimization_patterns` join rows, followed by `member_count` reconciliation. History count (5) now equals sum of active cluster `member_count` (5) as expected.
- **Routing: REST callers excluded from sampling, internal beats auto-sampling** — `_can_sample()` narrowed from `ctx.caller in ("mcp", "rest")` to `ctx.caller == "mcp"` so REST callers never reach sampling tier (regression from the provider-threading refactor). `resolve_route()` auto path now tries internal (tier 3) before auto-sampling (tier 4), matching the 5-tier priority table in `CLAUDE.md`. Passthrough fallback sets `degraded_from="internal"` (was `"sampling"`); auto-sampling (tier 4) sets `degraded_from="internal"` to signal the preferred tier was unavailable.
- **Tools delegate sampling path to dedicated pipeline functions** — `tools/analyze.py` imports `run_sampling_analyze` at module level and delegates to it when `tier=sampling`, returning early with `AnalyzeOutput` (fixes `AttributeError` when tests patched `app.tools.analyze.run_sampling_analyze`). `tools/optimize.py` imports `run_sampling_pipeline` at module level and delegates when `tier=sampling`, threading all enrichment fields (`codebase_context`, `task_type`, `domain`, `divergence_alerts`, `strategy_intelligence`, `profile`) to the pipeline. `sampling_pipeline.py` restored as a re-export layer (it was deleted by the sampling-unification commit); removed a stale `strategy_used` kwarg from a `HeuristicScorer.score_prompt()` call that `TypeError`d.
- **Graceful routing-init fallback in `tools/prepare` + `tools/save_result`** — new `_get_provider_safe()` helper wraps `get_routing()` in `try/except ValueError` so unit tests that don't initialize the routing singleton can still exercise `enrichment.enrich()` and heuristic-only scoring without a synthetic routing fixture. Production path unchanged — `provider` resolves to the live instance whenever routing is initialized.
- **Routing: `_write_optimistic_session` must not force `sampling_capable=True`** — both the VS Code bridge (sampling) and plain Claude Code (non-sampling) send session-less GET reconnects; the previous code called `on_mcp_initialize(sampling_capable=True)` for ALL reconnects, which (a) forced `sampling_capable=True` in memory and session file for non-sampling connections after VSCode disconnected, and (b) made `_inspect_initialize()` refuse to downgrade (stuck state). Fix preserves the current `routing.state.sampling_capable` unchanged and calls `on_mcp_activity()` to keep the connection alive without changing capability; the real initialize handshake that follows within ms sets the final value. Live-verified: `synthesis_optimize` now routes `pipeline_mode="internal"` after VSCode closure.
- **Cross-process `taxonomy_changed` events bridged into engine dirty_set** — MCP/CLI/test deletes publish `taxonomy_changed` with `affected_clusters`, but the backend listener only signaled the warm-path timer and never marked those clusters dirty on the resident engine; Phase 0 therefore skipped with `decision="no_dirty_clusters"`, leaving stale `member_count` on domain nodes until the next maintenance cadence reconciled them. New `_apply_cross_process_dirty_marks(engine, event_data)` helper is called before `_warm_path_pending.set()` so Phase 0's domain-node `phase_reconcile()` runs on the real dirty set.
- **Classifier B1 — ORM / framework noun coverage (SQLAlchemy factory misclass)** — "Design a SQLAlchemy async session factory with per-request dependency injection for FastAPI" classified as creative because only `design` scored and A2 disambiguation missed. Added compound signals to `_TASK_TYPE_SIGNALS["coding"]` (`session factory` 1.2, `dependency injection` 1.1, `connection pool` 1.0, `design a factory` 1.2, `build a factory` 1.2, `design a session` 1.1) and singles (`sqlalchemy` 0.7, `fastapi` 0.7, `django` 0.7, `flask` 0.6, `factory` 0.5, `session` 0.4); added `factory`/`session`/`sqlalchemy`/`fastapi`/`django`/`flask` to `_TECHNICAL_NOUNS`. Conservative — `app`/`tool`/`client` still excluded for their legitimate creative-writing use. Why it matters: profile selector gates `code_aware` on `task_type ∈ {coding, system, data}`, so a creative misclass skipped codebase context + applied_patterns entirely.
- **Classifier B2 — technical-signals rescue path into `code_aware` profile** — analysis verbs and creative misclassifications on prompts that clearly reference a linked codebase ("Audit the routing pipeline for race conditions") lost curated retrieval + pattern injection even when B1's classifier fix would route them correctly. `select_enrichment_profile()` now upgrades any task type to `code_aware` when `technical_signals=True` AND `repo_linked`. New `has_technical_nouns(first_sentence)` pure helper in `task_type_classifier.py` takes a noun-only hit against the same `_TECHNICAL_NOUNS` frozenset, recording `technical_signals_detected=True` in `enrichment_meta` so inspectors can see why `code_aware` engaged on a non-coding task_type. Cold-start gate still wins.
- **Classifier B6 — preserve single-word signal defaults through `set_task_type_signals()` merge** — `set_task_type_signals()` rebuilt the merged table from `_STATIC_COMPOUND_SIGNALS` + dynamic singles only; `_STATIC_COMPOUND_SIGNALS` filters to multi-word entries, so single-word defaults had no fallback tier. Any warm-path invocation — even with a single-task-type dynamic payload — dropped the single-word coding signals needed by prompts like the SQL session-factory one. Added `_STATIC_SINGLE_SIGNALS` module-level snapshot (mirror of `_STATIC_COMPOUND_SIGNALS`) capturing single-word defaults at import; dynamic payload wins for task types with live extraction; otherwise fall back to `_STATIC_SINGLE_SIGNALS` so unextracted types retain B1/A8 defaults.
- **Pattern injection — unscoped clusters visible inside project filter (A10)** — `embedding_index.search()` compared `project_ids[label] == project_filter`, which evaluated `None == '<project>'` as False; brand-new clusters before warm Phase 0 had run their member-distribution reconciliation were invisible to their own project's pattern injection. Now treat `project_ids[label]=None` as "unreconciled, visible within any scope" — return True when `lp == project_filter OR lp is None`. Cross-project bleed is still blocked (verified by new regression test).
- **Pattern injection provenance — manual expunge replaced with `begin_nested()` SAVEPOINT** — previous code used `db.add()` + manual `db.expunge()` when provenance INSERT failed on FK IntegrityError (optimization row not yet committed); if `expunge()` itself failed, a `PendingRollbackError` poisoned the entire `AsyncSession`, cascading into Phase 2 (optimize) failures. Each provenance block now wraps in `db.begin_nested()` so only the savepoint rolls back on FK failure — the outer `AsyncSession` stays clean for subsequent pipeline phases. Removes all manual expunge logic.
- **`domain_signals` runner-up suppression when it would outscore the resolved domain** — `_build_domain_signals_block` only checked the *gap* (within 0.15 margin), not the direction. When `reconcile_domain_signals` re-anchored to an LLM-assigned domain with zero heuristic score, the gap check was trivially satisfied by any positive runner — including one at 1.0, producing `devops 0.00 ~ backend 1.00` inverted rows in the Inspector. Fix adds two bounds: `runner_up` only emits when (a) `best_runner <= top_score` AND (b) `best_runner > 0`. Heuristic disagreement still lives in `heuristic_domain_scores` as evidence.
- **N1/N2/N3 code-review nits** — N1: `injection_clusters` dedup now keys on `cluster_id` instead of `cluster_label` (labels can legitimately be empty for new/untitled clusters, which silently collapsed distinct clusters into one); N2: TASK-TYPE SCORES block guarded with `nonZero.length > 0` so all-zero vectors don't render an orphan heading + lonely `others: 6 × 0.0` row; N3: `auto_resolve_repo()` in `tools/_shared.py` delegates to `resolve_effective_repo()` — single AA1 resolution path for REST + MCP.
- **Test assertions updated to weighted `overall_score` mean** — scoring migrated to the v3 dimension weights (`faithfulness 0.26`, `clarity 0.22`, `specificity 0.22`, `structure 0.15`, `conciseness 0.15`) but several test assertions hardcoded the old arithmetic mean; updated so the suite accepts the weighted reality instead of masking it with flakes.
- **Migration `2f3b0645e24d` rewritten — create `global_patterns` when missing, drop drift-correction cruft** — `GlobalPattern` was declared in `app/models.py` (ADR-005, commit `541e964d`) but no migration ever created the `global_patterns` table; live dev DBs only had it because `Base.metadata.create_all()` runs at startup. Fresh DBs bootstrapped purely via `alembic upgrade head` (CI migration tests, Docker cold starts) were missing the table entirely, causing every migration-chain smoke test to fail at `batch_alter_table('global_patterns')`. The task-type-telemetry migration is now idempotent (inspector-gated `create_table` on both `global_patterns` and `task_type_telemetry`) and also drops the auto-generated drift ops that mistakenly undid `cc9c44e78f78`'s hotpath-index creation (dropping `ix_optimizations_*` + `ix_feedbacks_optimization_id` right after adding them) and applied cosmetic TEXT↔JSON / REAL↔Float column alters SQLite ignores anyway. Net effect: fresh DBs land at the intended schema; dev DBs are unaffected (alembic records the revision as applied, body is never re-run); migration test chain is green.
- **`test_delete_publishes_optimization_deleted_event` — defensive `_shutting_down` reset** — the new DELETE-event test did not reset `event_bus._shutting_down` and was therefore vulnerable to leakage from lifespan tests run earlier in the suite (when `_shutting_down=True`, `publish()` is a no-op). Added the same defensive reset that `test_optimization_service_delete.py`, `test_project_migration.py`, `test_projects_router.py`, and `test_readiness_crossings_e2e.py` already carry.

## v0.4.1 — 2026-04-20

### Added
- **Sidebar brand audit finale — Navigator 2,692 → 182 lines via 8-panel extraction** — `Navigator.svelte` now delegates to eight focused panels (`StrategiesPanel`, `HistoryPanel`, `GitHubPanel`, `SettingsPanel`, `ClusterRow`, `DomainGroup`, `StateFilterTabs`, `TemplatesSection`) under `components/layout/`. `ClusterNavigator.svelte` split into per-domain row components. `CollapsibleSectionHeader` gains Snippet-based whole-bar/split modes. `ActivityBar` gets a sliding indicator + accessibility polish; `Inspector` gets a phase-dot indicator. Brand-token sweep across editor, refinement, shared, and taxonomy components.
- **Inspector.svelte split — 3 sections extracted** — `ClusterPatternsSection.svelte` (103 lines, meta-patterns list + 5 context-aware empty states), `ClusterTemplatesSection.svelte` (70 lines, cluster-scoped templates — named to disambiguate from the proven-templates Navigator section), `TaxonomyHealthPanel.svelte` (123 lines, idle-state Q_health/coherence/separation panel with sparkline). `Inspector.svelte` 1,404 → 1,165 lines total. Dead-code cleanup (unused `feedback = $derived(...)`).
- **`pipeline_phases.py` — phase helpers extracted from pipeline orchestrator (Phase 3D)** — 12 pure helpers (`resolve_blocked_strategies`, `resolve_post_analyze_state`, `build_optimize_context`, `run_hybrid_scoring`, `run_suggestion_phase`, `persist_and_propagate`, `build_pipeline_result`, `persist_failed_optimization`, plus typed result dataclasses `PostAnalyzeState`/`OptimizeContextBundle`/`ScoringOutput`/`PersistenceInputs`). `pipeline.py` 1,146 → 610 lines. SSE event yields stay in the orchestrator; pure computation moves to the helpers.
- **`sampling/` subpackage — primitives + persistence + analyze (Phase 3B)** — `sampling_pipeline.py` 1,705-line monolith split into three focused modules: `sampling/primitives.py` (401 lines, MCP request primitives + text/JSON extraction + fallback parsers), `sampling/persistence.py` (165 lines, DB helpers: applied-pattern resolution, usage counters, drift check), `sampling/analyze.py` (332 lines, standalone `run_sampling_analyze` two-phase entry point). Re-exports preserve the public API.
- **`batch_orchestrator.py` + `batch_persistence.py` — seed pipeline split (Phase 3E)** — 1,077-line `batch_pipeline.py` split: `batch_orchestrator.py` (243 lines, parallel `run_batch` + one-shot 429 backoff + per-batch invariants), `batch_persistence.py` (310 lines, quality-gated `bulk_persist` with `optimization_created` emission + `batch_taxonomy_assign` with deferred pattern extraction), residual `batch_pipeline.py` (645 lines) retains `PendingOptimization`, `run_single_prompt`, `estimate_batch_cost`. Re-exports preserve the public API.
- **`repo_index_outlines.py` + `repo_index_file_reader.py` + `repo_index_query.py` — indexing split (Phase 3C)** — 1,676-line `repo_index_service.py` split along three seams: `repo_index_outlines.py` (271 lines, pure FileOutline extractors + embed-text builders + content-SHA hashing, no DB/network), `repo_index_file_reader.py` (268 lines, read+embed pipeline + module-level file-content TTL cache), `repo_index_query.py` (695 lines, `RepoIndexQuery`: relevance search, curated context, import-graph expansion, source-type balancing, budget packing + curated TTL cache), residual `repo_index_service.py` (755 lines) retains `RepoIndexService` lifecycle (build/incremental/invalidate/CRUD).
- **`task_type_classifier.py` + `domain_detector.py` + `weakness_detector.py` — heuristic analyzer split (Phase 3F)** — 929-line `heuristic_analyzer.py` split into a thin orchestrator (635 lines) over three sub-modules: `task_type_classifier.py` (369 lines, A1 keyword signals + A2 verb/noun disambiguation + A4 Haiku LLM fallback, owns `_TASK_TYPE_SIGNALS` singleton + `get_task_type_signals`/`set_task_type_signals`), `domain_detector.py` (109 lines, `DomainSignalLoader` shim + `classify_domain` + `enrich_domain_qualifier`), `weakness_detector.py` (164 lines, pure keyword pattern detection). Re-exports + test-patch targets preserved.
- **`repo_relevance.py` + `divergence_detector.py` + `strategy_intelligence.py` — context enrichment split (Phase 3A)** — 1,394-line `context_enrichment.py` split along four concerns: `repo_relevance.py` (232 lines, `_GENERIC_TERMS`, `extract_domain_vocab`, `compute_repo_relevance`), `divergence_detector.py` (186 lines, canonical `_TECH_VOCABULARY` + `_COMPAT_PAIRS` + `detect_divergences`), `strategy_intelligence.py` (215 lines, `resolve_strategy_intelligence` with C1 domain-relaxed fallback + E1 hit-rate tracking). Residual `context_enrichment.py` retains orchestration (`ContextEnrichmentService.enrich()`, profile selection, `EnrichedContext` frozen dataclass).
- **UI persistence through stores — `hints.svelte.ts` + `topology-cache.svelte.ts` + `githubStore.uiTab`** — Phase 2 of the code-quality sweep moves localStorage out of component code. `GitHubPanel` tab persistence uses `githubStore.uiTab` / `setUiTab()` instead of ad-hoc `$effect`. `TopologyControls` pattern-graph onboarding hint dismissal moves to `stores/hints.svelte.ts` with one-shot migration from the legacy `synthesis:pattern_graph_hints_dismissed` key. `SemanticTopology` 60-iteration settled-position cache moves to `stores/topology-cache.svelte.ts` with fingerprint-based single-entry staleness policy.
- **`utils/keyboard.ts` + `utils/transitions.ts` — brand-aligned UI primitives** — `keyboard.ts` exports pure `nextTablistValue()` + `handleTablistArrowKeys()` for tablist arrow-key navigation (wrap, orientation, no-op branches). `transitions.ts` exports `navSlide`/`navFade` presets driven by an inline 8-iteration Newton-Raphson bezier solver that matches `--ease-spring` (`cubic-bezier(0.16, 1, 0.3, 1)`) exactly — Svelte's built-in `cubicOut` was materially flatter and drifted from CSS-driven transitions.
- **Frontend test coverage for extracted utilities and Navigator panels (PR #33 HIGH)** — `keyboard.test.ts` (23 cases, arrow-key logic across orientation + wrap + edge cases + wrapper `preventDefault` behaviour); `transitions.test.ts` (7 cases, preset duration/easing + Newton-Raphson solver clamps + monotonicity + shape at `x=0.25/0.5`); 8 Navigator panel smoke tests (`StateFilterTabs`, `ClusterRow`, `DomainGroup`, `TemplatesSection`, `StrategiesPanel`, `HistoryPanel`, `GitHubPanel`, `SettingsPanel`) covering mount-under-states, store-driven rendering, lazy-fetch gating, per-panel interaction contracts.
- **Unit tests for score_blender + pattern_injection (Phase 5A)** — `test_score_blender.py` (274 lines) + `test_pattern_injection_unit.py` (349 lines) hoist hot-path logic into dedicated units so regressions surface without a full pipeline run.
- **Unit tests for gc + error_logger (Phase 5C)** — `test_gc.py` (394 lines, `_gc_failed_optimizations` cascade + `_gc_archived_zero_member_clusters` safety gates + `_gc_orphan_meta_patterns` NOT-IN cleanup) + `test_error_logger.py` (216 lines) lift two startup-only infra modules to 100% branch coverage.
- **PRAGMA event hook on every pool checkout** — `backend/app/database.py` registers a `@event.listens_for(engine.sync_engine, "connect")` listener that applies `journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`, `cache_size=-64000`, and `foreign_keys=ON` to every SQLite pool connection. Replaces the throwaway aiosqlite block at startup that set these PRAGMAs on a single connection and discarded it — `busy_timeout`, `foreign_keys`, `synchronous`, and `cache_size` are per-connection and reset to SQLite defaults on every pool checkout, so the old approach silently regressed concurrency safety + FK enforcement. `pool_pre_ping=True` + `pool_recycle=3600` restored on the engine. Regression from PR #1 audit.
- **Recurring GC sweep — hourly expired-token + orphan-repo cleanup** — new `run_recurring_gc()` in `backend/app/services/gc.py` + `_recurring_gc_task` scheduled in `lifespan` (`main.py`). Sweeps expired `GitHubToken` rows (both access + refresh expired, 24h grace for in-flight refreshes, legacy non-expiring tokens preserved) and orphan `LinkedRepo` rows (no matching `GitHubToken.session_id`). Previously the GC functions ran once at startup, so tokens + orphan repos accumulated indefinitely between restarts. Regression from PR #1 audit.
- **Hotpath indices migration** — `alembic/versions/cc9c44e78f78_add_hotpath_indices.py` creates seven single-column indices on `optimizations` (every `OptimizationService.VALID_SORT_COLUMNS` entry except the PK) + composite `ix_optimizations_project_created(project_id, created_at DESC)` + `ix_feedbacks_optimization_id`. Guarded with `_has_index()` inspector check, symmetric `downgrade()`. Regression from PR #1 audit.

### Changed
- **CSS timing tokens — `--duration-skeleton` (1500ms) + `--duration-stagger` (350ms)** — `ClusterRow`, `HistoryPanel`, and `TierGuide` replace hardcoded `1500ms` / `350ms` animation literals with the new tokens in `app.css`. `ForgeArtifact` replaces three `{duration: 200}` overrides with the shared `navSlide` preset.
- **`OptimizationResult.optimized_scores` typed** — schema narrowed to `DimensionScores | null` legacy alias; drops two `as any` casts in `forge.svelte.ts` in favour of a typed normalization path.
- **Module-level store imports retained in extracted Navigator panels (architectural decision)** — evaluated the MEDIUM finding to refactor the 8 panels toward stores-via-props dependency injection. Decision: retain current pattern. Svelte 5 runes-backed stores (`.svelte.ts` modules exporting singleton class instances) are the idiomatic boundary, and the smoke-test suite proves each panel is unit-testable in isolation without DI — tests mutate singleton state in `beforeEach` and assert render output without touching the panel's imports. See `frontend/CLAUDE.md` → "Key patterns".

### Fixed
- **`transitions.ts` easing fidelity — Newton-Raphson bezier solver for true `--ease-spring`** — the navigator slide/fade presets previously used Svelte's built-in `cubicOut` (`cubic-bezier(0.33, 1, 0.68, 1)`), materially flatter than the brand's `--ease-spring` (`cubic-bezier(0.16, 1, 0.3, 1)`). JS-driven transitions drifted visibly from CSS-driven ones. Replaced with an inline 8-iteration Newton-Raphson solver for the exact `--ease-spring` control points — single source of truth for the brand spring across JS and CSS.
- **`optimization_failed` event payload restored** — Phase 3D pipeline split silently dropped the `error` field when extracting `persist_failed_optimization()` into `pipeline_phases.py`. `error_message` threaded through so SSE subscribers observe the same payload shape as before the split.
- **`heuristic_analyzer.py` reads `settings.MODEL_HAIKU` directly** — drops the `getattr` fallback at line 616; the Pydantic `Field` already guarantees the attribute. Dead defensive code removed.
- **ROADMAP snapshot line — `v0.3.41-dev` → `v0.4.1-dev`** — drift fix after the v0.4.0 cut.
- **Test-patch targets updated after sampling + pipeline splits** — `async_session_factory` patch targets re-pointed at the new module boundaries (`patch("app.services.sampling_pipeline.async_session_factory")` → `patch("app.services.sampling.persistence.async_session_factory")`, etc.); import ordering resorted to satisfy `ruff I001`; unawaited-coroutine warnings in sampling + mcp tool tests suppressed at the pytest configuration layer.
- **PR #34 MEDIUM findings closed** — coverage + structural nits surfaced by the code-reviewer during the store-routing review addressed.

### Removed
- **Soft-delete retired explicitly** — PR #1 shipped a `deleted_at: Mapped[datetime | None]` column on `Optimization` / `Feedback` / `LinkedRepo` with `where(deleted_at.is_(None))` filters. The v2 rebuild excised the column set; this entry documents the decision as intentional rather than accidental. Rationale: (1) no UI for undelete ever existed, (2) the archive-as-soft-delete pattern covers the legitimate undelete cases via `cluster.state='archived'`, (3) hard-delete is simpler for GDPR. No code change — clarifying note.

## v0.4.0 — 2026-04-19

### Added
- **ADR-005 Hybrid Taxonomy — projects as sibling roots (8-commit shipment, `ab07fd30…c1ab12f7`, 2026-04-19)** — supersedes the original ADR-005 "project as tree parent" data model. Projects now live at `parent_id IS NULL` alongside domain nodes; clusters parent to domains and carry `dominant_project_id` as a denormalized view-filter FK. Full breakdown:
  - **S1 schema (`ab07fd30`)** — migration `d9e0f1a2b3c4_add_dominant_project_id_to_prompt_cluster.py` adds `prompt_cluster.dominant_project_id` (nullable FK, self-referential to project rows) with a partial index `ix_prompt_cluster_dominant_project_id WHERE dominant_project_id IS NOT NULL` for the hot tree/stats filter path.
  - **B1 pipeline (`a51f8a10`)** — `pipeline.py` freezes `project_id` at request time via `resolve_project_id()` (repo → `LinkedRepo.project_node_id` → project row). New module-level cache (`_cached_legacy_project_id` in `project_service.py`) memoizes the Legacy fallback lookup so every zero-repo optimization skips the DB round-trip.
  - **B2–B5 migration + link/unlink (`df00ef9e`)** — `POST /api/projects/migrate` (body-driven — `{source_project_id, target_project_id, dry_run}`) reparents a whole project's clusters and rewrites `dominant_project_id` + `Optimization.project_id` under a single transaction; `LinkedRepo` link creates a project node automatically on first connect; `DELETE /api/repos/linked?mode=keep|rehome` — `keep` (default) leaves `Optimization.project_id` untouched so history stays attributed to the repo's project; `rehome` migrates the last 7 days of optimizations back to Legacy. Neither mode clears `repo_full_name` on existing rows.
  - **B6 hybrid tree/stats (`1425274c`)** — `GET /api/clusters/tree?project_id=...` and `GET /api/clusters/stats?project_id=...` now SQL-scope via `WHERE dominant_project_id = :pid OR state IN ('project','domain') OR id = :pid`. Tree excludes bare project-less clusters so cross-project contamination is visually impossible.
  - **B7–B8 patterns (`aabe9f85`)** — `auto_inject_patterns()` filters MetaPatterns by `source_cluster → dominant_project_id` so cross-project noise cannot leak into per-project rankings; global promotion gate enforces BOTH `GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS` (5) cross-cluster breadth AND `GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS` (2) distinct source projects. A pattern that lives inside a single project — no matter how many clusters it spans there — stays project-scoped until a sibling emerges in another project.
  - **C1a–C1c warm/cold maintenance (`b06e8b3c`)** — warm Phase 0 reconciles `dominant_project_id` from `LinkedRepo` + recent `Optimization.project_id` majority vote; cold path rewrites `dominant_project_id` during split/merge. `CROSS_PROJECT_REASSIGN_MARGIN = 0.10` cosine margin enforces same-project preference — a cluster only migrates to a different project if the cosine to the new centroid exceeds the incumbent by ≥0.10.
  - **F1–F5 frontend (`c145f96d`)** — `projectStore` (`frontend/src/lib/stores/project.svelte.ts`) with `currentProjectId` rune (`null` = "All projects"; survives repo unlink, persisted to `localStorage['synthesis:current_project_id']`), project selector UI, every mutating call (`/api/optimize`, `/api/refine`, `synthesis_optimize`) now threads an explicit `project_id`, tree/topology/pattern consumers subscribe to the store, link/unlink transition toasts via `applyLinkResponse(projectId, candidates)` (stashes `lastMigrationCandidates` for the F5 migration prompt), per-project Inspector breakdown.
  - **Locked-decision record**: `docs/hybrid-taxonomy-plan.md` (2026-04-19) is the canonical design doc. ADR-005 Amendment 2026-04-19 links out rather than duplicating.
  - Still deferred: Phase 3 HNSW (triggered at ≥1000 clusters via `HNSW_CLUSTER_THRESHOLD`; codepath exists, currently dormant).
- **Opus 4.7 provider feature surface — `xhigh` effort, `display: "summarized"` adaptive thinking, Task Budgets beta, Compaction beta** — full 4.7 capability set wired through the provider ABC (`backend/app/providers/base.py`), the `AnthropicAPIProvider` (`anthropic_api.py`), the `ClaudeCLIProvider` (`claude_cli.py`), the preferences validation layer (`services/preferences.py` + `routers/preferences.py`), and the Navigator effort selector (`frontend/src/lib/components/layout/Navigator.svelte`):
  - **`xhigh` effort** — new level between `high` and `max`, Opus 4.7 only per Anthropic docs. Accepted everywhere the other levels are accepted: `VALID_EFFORTS` set extended, `_PipelineUpdate` Pydantic model now `Literal["low", "medium", "high", "xhigh", "max"] | None` on `analyzer_effort` / `optimizer_effort` / `scorer_effort`, Navigator dropdown gains an `<option value="xhigh">` between high and max. Both providers detect non-Opus-4.7 models by `"opus-4-7" not in model.lower()` and downgrade `xhigh → high` with a warning log to prevent 400s on Sonnet/Haiku. New static helper `LLMProvider.supports_xhigh_effort(model)` centralizes the gate.
  - **`display: "summarized"` adaptive thinking** — `LLMProvider.thinking_config(model)` now returns `{"type": "adaptive", "display": "summarized"}` on Opus 4.7 (was plain `{"type": "adaptive"}`). Opus 4.7's SDK default is `display: "omitted"` — streamed `thinking` blocks arrive with empty text. Without this override, streaming UIs observe a long silent pause before any output appears. Opus 4.6 / Sonnet 4.6 continue returning plain adaptive (no `display` key) since they still default to visible thinking.
  - **Task Budgets beta** — new `task_budget: int | None = None` parameter on `LLMProvider.complete_parsed()`, `complete_parsed_streaming()`, and `call_provider_with_retry()`. Opus 4.7 only (silent no-op on other models). `AnthropicAPIProvider._build_kwargs()` wires it via `output_config.task_budget = {"type": "tokens", "total": N}` and adds the `task-budgets-2026-03-13` beta header. Clamped upward to the SDK-enforced 20,000-token minimum (values below are silently raised, preserving caller intent). Semantically distinct from `max_tokens`: `max_tokens` is a per-response hard ceiling the model is not aware of; `task_budget` is a full-loop allowance the model sees as a running countdown and self-moderates against — the correct knob for agentic pipelines.
  - **Compaction beta** — new `compaction: bool = False` parameter on the same three provider surfaces. Opus 4.7 / Opus 4.6 / Sonnet 4.6 only (silent no-op elsewhere — Haiku and older models do not support it). Wired via `context_management.edits = [{"type": "compact_20260112"}]` + beta header `compact-2026-01-12`. Distinct-header discipline: when both Task Budgets and Compaction are enabled on the same request, the two `anthropic-beta` header values are comma-joined into a single `extra_headers["anthropic-beta"]` string rather than overwriting each other.
  - **`ClaudeCLIProvider` ABC uniformity** — CLI provider accepts both new kwargs as explicit no-ops (documented in the class docstring: "accepted, currently a no-op — the CLI does not surface those knobs"). Keeps the `LLMProvider` ABC signature uniform across providers so pipeline callers never need to branch on provider type to decide whether to pass the Opus 4.7 kwargs. xhigh-downgrade logic mirrors the API provider so CLI routes stay consistent on non-4.7 models.
  - Test coverage: 11 new cases in `tests/test_providers.py` (`TestSupportsXhighEffort` class + xhigh downgrade + task_budget wiring / clamping / model-gating + compaction wiring / model-gating + combined-beta-header sharing + CLI-provider xhigh parity) and 3 new cases in `tests/test_preferences.py` (xhigh acceptance across analyzer/optimizer/scorer). 102 total provider+preference tests green.
- **`synthesis_health` MCP tool — `linked_repo` block** (`482cf245`) — the health tool now returns `full_name`, `branch`, `language`, `index_status`, `index_phase`, `files_indexed`, `synthesis_ready` for the active linked repo, mirroring `auto_resolve_repo()` resolution order. Before this change the MCP surface had no way to confirm whether a GitHub repo was linked; callers had to run an optimize and inspect the stored record to reason about codebase-context absence. Single health call now answers the question end-to-end.
- **Per-agent seed model override + per-dispatch JSONL trace** (`660db4a8`) — seed agents default to Haiku for cheap parallel generation, but specific agents can now opt into Sonnet (or Opus) for diversity A/B tests via YAML frontmatter `model: sonnet|opus|haiku` on the agent file in `prompts/seed-agents/*.md`. `SeedAgent.model` added to `agent_loader.py`; `_resolve_agent_model()` in `seed_orchestrator.py` maps the frontmatter to `settings.MODEL_*` with case-insensitive validation and safe fallback to Haiku on unknown values. Each dispatch emits a `phase="seed_agent"` JSONL trace with `trace_id="seed:{batch_id}:{agent}"`, duration, tokens, resolved model, and success/error (with exception type). Zero behavior change at merge — no agent opts in yet. Trace emission is best-effort (wrapped in try/except) and never breaks the dispatch it observes.
- **Explore synthesis routed to Sonnet + JSONL trace per run** (`182f95df`) — `CodebaseExplorer._explore_inner` synthesizes 30–80K-token file payloads into a ~500-word architectural summary. Long-context reading comprehension at that scale is Sonnet's strength and Haiku 4.5's weakness. Result is cached per repo/branch/SHA in `RepoIndexMeta.explore_synthesis`, so Sonnet runs ~once per repo link, not per request — cost delta is negligible, synthesis quality improves materially. Each run emits a `phase="explore_synthesis"` trace via `TraceLogger.log_phase(trace_id="explore:{repo}@{branch}@{sha[:8]}", ...)`. Trace emission wrapped in try/except. `MODEL_HAIKU` config description updated (no longer claims "explore").
- **Phase 0 orphan-structural-node sweep** — warm-path reconcile now archives empty domain / sub-domain nodes that have 0 active-cluster children AND 0 active sub-domain children AND 0 optimization references, once they cross a `ORPHAN_STRUCTURAL_GRACE_HOURS=24` age floor (new constant in `_constants.py`). Fixes the zero-prompt "ghost Legacy 1m 0 --" visibility bug where the ADR-005 migration created a Legacy project node + child `general` domain, which kept the project row inflated at `member_count=1` forever because nothing reaped the empty domain. Guards: `general` is never archived if it still has structural descendants or opt refs; young nodes are exempt via `created_at` cutoff; cluster OR sub-domain children both count as occupancy; direct optimization references (`Optimization.cluster_id`) count as occupancy. Best-effort cleanup on `EmbeddingIndex.remove()` + `QualifierIndex.remove()` + `DomainResolver.remove_label()` (wrapped in broad `except Exception:` so uninitialized resolvers in test contexts can't abort the sweep). Project ancestor `member_count` is re-reconciled in the same pass, so the UI surfaces the new reality on the next topology refresh. New `ReconcileResult.orphan_structural_nodes_archived` counter. 5 new RED→GREEN tests in `tests/taxonomy/test_warm_phases.py` lock all guard conditions + the positive archival path.

### Changed
- **`LLMProvider` ABC signature extended** — `complete_parsed()`, `complete_parsed_streaming()`, and `call_provider_with_retry()` now accept `task_budget: int | None = None` and `compaction: bool = False` in addition to the existing `effort` / `cache_ttl` kwargs. Default values keep every existing call site compatible — no caller changes required. Both concrete providers (`AnthropicAPIProvider`, `ClaudeCLIProvider`) forward the new params; CLI treats them as documented no-ops.

### Fixed
- **Heuristic classifier — analysis verbs, first-sentence boundary, meta-prompt + analysis compound keywords** — three drift sources resolved in sequence on the same prompt pipeline:
  - **Missing analysis verbs**: `_TASK_TYPE_SIGNALS["analysis"]` had no entries for common inspection synonyms. Prompts leading with "Audit …", "Diagnose …", or "Inspect …" scored 0 on analysis and fell back to `general`. Added `audit`/`diagnose` at weight 0.9 and `inspect` at 0.8, matching the existing `evaluate`/`assess` scale.
  - **First-sentence boundary bug**: `first_sentence = prompt_lower.split(".")[0] if "." in prompt_lower else prompt_lower` only split on periods. Prompts ending in `?` or `!` with no trailing `.` had `first_sentence == whole_prompt`, so every keyword match received the 2x first-sentence positional boost, nullifying the signal. Replaced with `re.split(r"[.?!]", prompt_lower, maxsplit=1)[0]`.
  - **Meta-prompt + analysis compound keywords (`4634aca2`)**: single-word matches on `write`/`audit` dragged meta-prompts like "write a prompt that audits the X" into `writing` instead of `system`, and simple "audits the X" into `general`. Added compound-phrase signals (`"write a prompt"` → system, `"audits the"`/`"audit the"` → analysis) that cleanly outweigh the single-word collisions without needing a corrective fallback layer. 4 RED→GREEN tests in `TestAuditAndFirstSentenceBoundary` + compound-keyword tests lock the three fixes together.
- **B0 repo-relevance gate — project-anchored synthesis + path-enriched anchor, single cosine floor** — `ContextEnrichmentService.compute_repo_relevance()` reworked across two commits (`d5d1c191`, `c129bc7c`) to eliminate false-positive rejections of valid focused-subsystem prompts while killing the brittle vocabulary-overlap fallback layer:
  - **Project identity in the anchor**: the repo-anchor embedding now prepends `Project: {repo_full_name}\n` before the explore synthesis. Same-stack-different-project prompts (e.g. two SvelteKit repos with overlapping tech vocabulary) separate cleanly on project-name signal rather than requiring a repo-vocabulary gate.
  - **Path-enriched anchor**: up to 500 indexed file paths (fetched via `RepoFileIndex WHERE embedding IS NOT NULL ORDER BY file_path` with a lazy import) are appended as a `Components:\n{joined paths}` block after the synthesis. Stride-samples at 100 paths to stay inside MiniLM's 512-token window. Component-level prompts like "audit the topology panel" now latch onto the path list instead of the architecture-only summary.
  - **Single cosine floor**: `REPO_RELEVANCE_FLOOR` lowered 0.20 → 0.15 to reflect the richer anchor. `REPO_DOMAIN_MIN_OVERLAP` removed; domain-entity overlap is no longer a decision gate. Reason codes collapse to `{"above_floor", "below_floor"}`. `extract_domain_vocab()` retained with a new `file_paths` parameter for `domain_matches` attribution in `info_dict` (UI only, never gating).
  - **Observability**: `enrichment_meta["repo_relevance_anchor_paths"]` records the path count per request. `info_dict` still carries `cosine`/`threshold`/`decision`/`reason`/`domain_overlap`/`domain_matches`/`domain_vocab_size`.
  - Test coverage: `TestDomainVocabPathEnrichment` (6 cases) + `TestRepoRelevanceAnchorEnrichment` (4 cases) cover freq=1 path preservation, stride sampling at cap, absent-paths legacy-anchor parity, and path-token appearance in domain_matches.
- **Background task GC race in repo link/reindex — strand `synthesis_status='running'`** — `asyncio.create_task(_bg_index())` in both `POST /api/github/repos/link` (line 344) and `POST /api/github/repos/reindex` (line 549) was fire-and-forget with NO strong reference held. Python's asyncio event loop keeps only WEAK references to tasks, so a coroutine spawned this way can be garbage-collected mid-await. When the long explore-synthesis Haiku call ran inside `_bg_index()`, the task could be GC'd mid-flight, silently killing the background job. The DB was stranded at `synthesis_status='running'` with `index_phase='synthesizing'` forever — no SSE `index_phase_changed` event fired to flip the UI off "INDEXING", no exception logged, no `error` status persisted. Fix: added module-level `_background_tasks: set[asyncio.Task]` strong-ref holder and a `_spawn_bg_task()` helper that calls `asyncio.create_task()`, adds to the set, and registers `add_done_callback(_background_tasks.discard)` so entries clean up on success OR exception. Both call sites now use `_spawn_bg_task()`. 3 RED→GREEN tests in `test_background_task_lifecycle.py` lock: (1) the task survives aggressive GC while awaiting, (2) the helper returns the `Task` instance, (3) failed tasks are still removed from the set via the done-callback.
- **Inspector "no codebase context" warning chip** — when an optimization was run against a linked repo but `context_sources.codebase_context === false` (B0 repo-relevance gate fired, index not ready, or repo was absent at pipeline time), the Inspector surfaces a yellow chip below the Repo row reading "Context: no codebase context" with a tooltip explaining the B0 gate / index-readiness cause and recommending reindex + rerun. Before this change the state was invisible — users had to inspect `context_sources` in the DB to tell whether output was repo-grounded. The chip only renders when `activeResult.repo_full_name` is set (so non-repo prompts stay clean) and only when `codebase_context` is explicitly `false` (so prompts that ran before `context_sources` was populated stay clean). 3 RED→GREEN tests in `Inspector.test.ts` lock the positive render path and both negative paths (no repo, codebase_context true).
- **Cluster navigator MBR column semantic correctness** — the `clusterRow` snippet in `ClusterNavigator.svelte` rendered every `member_count` with an unconditional `m` suffix (short for "members"), which was a lie on project and domain rows because `member_count` is semantically overloaded: for clusters it counts optimizations, for domains it counts child clusters, for projects it counts child domains. Snippet now derives `mbrSuffix` + `mbrUnit` from `family.state`: `Nd` / "N domain(s)" for project rows, `Nc` / "N cluster(s)" for domain rows, `Nm` / "N member(s)" for cluster rows. Tooltip picks up the plural-aware unit. Two RED→GREEN tests in `ClusterNavigator.test.ts` lock the cluster ("5m") and project ("3d") suffixes.
- **`DomainResolver` — preserve high-confidence unknown labels** (`19159a21`) — the resolver was collapsing every unknown label to `"general"` pre-emptively, destroying the organic signal that warm-path domain discovery depends on. Now, when the blended LLM+heuristic confidence meets the gate, the resolver returns the primary label verbatim so the taxonomy can grow organically. Low-confidence unknowns still fall through to `"general"`.
- **`DomainResolver` preservation gate lowered 0.6 → 0.5** (`659ff085`) — `DOMAIN_CONFIDENCE_GATE` moved to the natural midpoint. Under the new "preserve by default" resolver semantics, the gate should only filter garbage/empty labels, not well-formed-but-under-confident ones (analyzer returning "frontend" at 0.55 blended confidence because task-type ambiguity dragged the overall score down). Preservation log elevated DEBUG → INFO so incubation churn stays visible in production. Boundary regression tests at 0.50–0.55 blended lock the new threshold.
- **Seed palette preservation on domain re-promotion** (`5967fbdd`) — the alembic migration seeds 8 canonical domains with brand-anchored OKLab colors (`backend=#b44aff`, `frontend=#ff4895`, `database=#36b5ff`, etc.). Per ADR-006 these get dissolved once they age past 48h with 0 members. When new prompts later arrive classified as backend/frontend, organic promotion via `_propose_domains()` rebuilt the domain node but assigned a fresh color via `compute_max_distance_color()` — the brand identity was lost across the dissolution cycle. Fix: `coloring.py` gains `SEED_PALETTE` (mirrors alembic `SEED_DOMAINS`) and `resolve_seed_palette_color(label)` (case-insensitive, whitespace-trimmed, returns `None` for unknown labels). `engine._create_domain_node()` consults the palette first for top-level promotions; if the label is canonical AND the seed color is not already in use, it's restored. Otherwise falls through to `compute_max_distance_color()` unchanged — novel labels still get proper OKLab distribution. ADR-006 semantics preserved: empty seed domains still dissolve, but "Backend is purple" stays true across re-promotion.
- **`DomainSignalLoader` bootstrap warning silenced on seed-only taxonomy** (`99cc39cd`) — the loader logged a misconfiguration warning whenever the DB only contained the seed `"general"` cluster, which is the expected state on a fresh install. Filter now excludes non-general domains from the check so the warning only fires on genuine misconfiguration.
- **Analyzer A4 LLM classification wrapped in retry** (`1365a930`) — the A4 LLM classification path in `HeuristicAnalyzer._classify_with_llm` was the only Haiku call site calling `provider.complete_parsed(...)` directly. Every other site uses `call_provider_with_retry()`, so transient rate-limit / overload errors at A4 silently failed to heuristic without a retry attempt. Wrapped with `call_provider_with_retry`; outer try/except preserved so final-attempt failures still degrade gracefully to the heuristic result.
- **Pipeline `data_dir` dependency injection — prevent production preferences bleeding into tests** (`087b307e`) — `PipelineOrchestrator.__init__` and `RefinementService.__init__` now accept `data_dir: Path | None = None` (defaulting to `DATA_DIR`) and thread it into both `TraceLogger` and `PreferencesService` construction. Previously the orchestrators hardcoded the top-level `DATA_DIR`, so test fixtures writing a `preferences.json` to `tmp_path` were silently ignored — the production `data/preferences.json` bled into every test via the fallback, observed as five pre-existing `TestPipelinePerformanceParams` failures where live effort=`max` overrode the documented defaults. Test isolation is now structural rather than mock-based; obsolete `patch("app.services.pipeline.DATA_DIR", ...)` monkeypatches removed. Zero runtime behavior change in production paths.

## v0.3.40 — 2026-04-19

### Added
- **Zero-LLM backup restore script** — `scripts/restore_from_backup.py` reads a pre-taxonomy backup DB and rehydrates optimizations into the current schema using only local embeddings + heuristic analyzer + cosine-based cluster assignment. Idempotent (SHA-16 dedupe on `raw_prompt`), supports `--dry-run`/`--limit`/`--force`, refuses to run while services are live. No LLM spend (~13s for 16 prompts).
- **Lazy preferences migration** — `PreferencesService._migrate_legacy_keys()` rewrites `enable_adaptation` → `enable_strategy_intelligence` on first load and persists the renamed file. Idempotent, preserves the stored value.
- **Explore per-call budget utilization log** — `codebase_explorer._explore_inner()` now emits `explore_budget: repo=… files=N payload_chars=X file_contents_chars=Y cap=Z utilization=P%` immediately before the Haiku call, and augments the existing `explore_synthesis:` line with `input_tokens=…` + `cache_read_tokens=…` pulled from `provider.last_usage`. Surfaces live usage against the empirical ~60K-token effective ceiling imposed by the CLI baseline, so live traffic confirms (or refutes) the static tuning. Regression test in `test_codebase_explorer.py::test_explore_logs_budget_utilization`.
- **Per-phase indexing state (`index_phase`) + SSE `index_phase_changed` event** — `RepoIndexMeta` gains three columns (`index_phase`, `files_seen`, `files_total`) that track the live pipeline cursor independently of the terminal `status` + `synthesis_status` pair. Phase transitions: `pending → fetching_tree → embedding → synthesizing → ready` (or `→ error` from any phase). `build_index()` writes `fetching_tree` before the tree fetch and flips to `embedding` before file processing; `_update_synthesis_status()` maps synthesis `running/ready/error/skipped` → `synthesizing/ready/error/ready`. Every transition publishes an `index_phase_changed` event carrying `phase`, `status`, `files_seen`, `files_total`, and `error`, so the frontend and bridge extension can stop misreporting "ready" while synthesis is still running and can surface errors as they happen. `/api/github/repos/index-status` returns the new fields. Migration `8ecce36187b5` (forward-only, inspector-guarded). Regression tests in `test_repo_index_service.py`.
- **Frontend phase visibility + error surface** — `githubStore.connectionState` expanded from 5 states to 7: `disconnected | expired | authenticated | linked | indexing | error | ready`. The `ready` gate now requires `index_phase === 'ready'` AND `status === 'ready'` AND synthesis complete, so the Navigator badge and StatusBar no longer flash "ready" while synthesis is still running. New `phaseLabel` getter renders human-readable copy per phase (`Fetching tree…`, `Embedding files…`, `Synthesizing context…`) and `indexErrorText` surfaces `error_message || synthesis_error` in a red row directly under the badge when the pipeline fails. SSE `index_phase_changed` events flow through `githubStore.applyPhaseEvent()` so the UI updates without waiting for the next 2-minute poll; `pollIndexStatus()` now breaks on both `ready` and `error`. Navigator badge adds a `--pulse` modifier during indexing and a dedicated error-row treatment; StatusBar mirrors the same state machine. `IndexStatus` type in `api/client.ts` extended with the new fields. Regression tests in `github.svelte.test.ts` lock the expanded state model.

### Changed
- **Repo-index caching layers** — four coordinated caches cut GitHub API pressure and embedder cost on incremental reindex and cross-branch/cross-repo workloads:
  - **Curated retrieval invalidation** — `incremental_update()` and `build_index()` now call `invalidate_curated_cache()` when index state changes, so a rebuild never serves a 5-min-stale context bundle. The lying `repo_full_name=` scope argument was dropped (it was silently ignored); a runtime signature test locks the zero-arg surface.
  - **GitHub tree ETag conditional fetches** — new `GitHubClient.get_tree_with_cache()` issues `If-None-Match` when `RepoIndexMeta.tree_etag` is populated. On `304` the tree body is skipped entirely and `incremental_update()` short-circuits via a new `tree_unchanged` skipped-reason. Forward-only migration `b549943bc7bd` adds `repo_index_meta.tree_etag`.
  - **Content-hash embedding dedup** — new `repo_file_index.content_sha` column (SHA-256 of the exact `embed_text` — path + outline + doc_summary, canonicalised through `_build_content_sha()`). `_read_and_embed_files()` batch-loads existing vectors for those SHAs (`SELECT ... WHERE content_sha IN (...) AND embedding IS NOT NULL`) and reuses them via `np.frombuffer(...).copy()`. Dedupes across branches, across repos that share vendored files, and across reindex cycles. The hash input is model-fenced — including `settings.EMBEDDING_MODEL` so a model upgrade automatically invalidates every persisted vector. Forward-only migration `c7d8e9f0a1b2`.
  - **File-content TTL + FIFO cache** — module-level `_file_content_cache` keyed by `(repo_full_name, path, file_sha)` (intentionally NOT by branch — git blob SHAs identify content exactly). 15-min TTL, 2048-entry cap, oldest-10% FIFO eviction on overflow. `_read_file()` consults the cache before hitting `get_file_content`. Vendored-file reads and repeat-reindex cycles now avoid redundant GitHub calls.
- **Preference key renamed: `enable_adaptation` → `enable_strategy_intelligence`** — the gate now matches the function it controls (`resolve_strategy_intelligence()`) and the template variable (`{{strategy_intelligence}}`). Fallback shims at 4 read sites removed; the nested `prefs.get(new, prefs.get(old, snapshot))` form was returning `None` in every branch and has been replaced with direct single-key reads. `_PipelineUpdate` PATCH schema gains the missing `enable_llm_classification_fallback` field. UI toggle label shortened to "Strategy Intel" to fit the compact card-terminal tier.
- **Setting renamed: `MAX_ADAPTATION_CHARS` → `MAX_STRATEGY_INTELLIGENCE_CHARS`** — mirrors the preference-key rename. `.env.example` updated.
- **Shared file-exclusion filters** — new `backend/app/services/file_filters.py` is the single source of truth for `INDEXABLE_EXTENSIONS`, `TEST_DIRS`, `TEST_SUFFIXES`, `TEST_INFRA`, `MAX_FILE_SIZE`, `is_test_file()`, and `is_indexable()`. `repo_index_service` and `codebase_explorer` both import from it, so the embedded corpus and the per-request Haiku synthesis apply identical exclusions. Closes three drift-induced gaps:
  - `codebase_explorer` previously filtered by extension only — test files, `.github/workflows/*.yaml`, and oversized files were packed into the explore prompt, wasting Haiku's tight CLI-adjusted budget and contributing to the "Explore returned empty result" failures.
  - `*.config.mjs` / `*.config.cjs` variants of `jest`, `vitest`, `playwright`, and `cypress` are now recognised as test infra (previously only `.js` / `.ts` matched).
  - Generated lock files with indexable extensions (`package-lock.json`, `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `composer.lock`) are rejected by exact-basename match; `.github/workflows/`, `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE/`, `.vscode/`, and `.idea/` are rejected by path-prefix.
  - 88-test pytest module (`tests/test_file_filters.py`) locks the behaviour against future drift. `GITHUB_TOOLTIPS.indexed_file_count` in `ui-tooltips.ts` updated to enumerate the full exclusion matrix.

### Fixed
- **GitHub connection state — casing + verbosity parity between Navigator and StatusBar** — same `connectionState` was rendering two different ways: StatusBar in raw case (`linked`, `index error`, `Fetching repo tree…`) vs Navigator's uppercase compact badges (`LINKED`, `ERROR`, `INDEXING`). StatusBar now carries a `.status-github--token` modifier (uppercase + matched letter-spacing) on every state-label branch except `ready`, which stays natural-case because it's a data value (repo name). `error` branch emits the compact `error` token (not `index error`); `indexing` branch emits the compact `indexing` token with the verbose `phaseLabel` relegated to the tooltip. Navigator's indexing branch mirrors the same tooltip/compact-token split so both surfaces are pixel- and token-identical. Regression: 3 RED tests in `StatusBar.test.ts` (uppercase class, compact `error`, compact `indexing`) + 1 in `Navigator.test.ts` (compact `indexing`), each verified to fail before the fix.
- **Explore synthesis silent failure** — the GitHub sidebar showed `Synthesis: error` with tooltip "Explore returned empty result" and no further diagnostic. Two distinct issues, both resolved:
  - **Error-visibility bug**: `ClaudeCLIProvider.complete_parsed()` read only `stderr` on non-zero exit, but the CLI writes structured error envelopes (`{"is_error": true, "api_error_status": 400, "result": "Prompt is too long"}`) to *stdout* with empty stderr. The real cause was swallowed — callers saw only "Claude CLI exited with code 1: ". Provider now parses the stdout JSON envelope when stderr is empty, emits `f"HTTP {api_error_status}: {result}"`, and honours `429/529/503` api_error_status for `retryable=True`. Two regression tests cover stdout-envelope extraction and retryability.
  - **Context-window miscalibration**: `EXPLORE_MAX_CONTEXT_CHARS=700000` was predicated on Haiku's raw 200K-token window (~700K chars). But `claude -p` prepends Claude Code's internal system prompt, which consumes **~140K of the 200K tokens** (visible as `cache_read_input_tokens: 94229 + cache_creation_input_tokens: 47954` in the CLI usage envelope). That leaves only ~60K tokens (~250–300K chars) for user content. Empirically measured via CLI probe: 300K chars accepted, 320K rejected with `prompt_too_long`. `EXPLORE_MAX_CONTEXT_CHARS` set to **250,000** to stay comfortably under the effective ceiling even with raw_prompt (20K max), template boilerplate, JSON schema, and line-number prefixes.
- **PreToolUse hook noise** — the two separate `.claude/hooks/pre-pr-{ruff,svelte}.sh` hooks fired on *every* Bash tool call and matched `git push` / `gh pr create` by raw substring, which produced false positives on innocent commands like `grep -r "git push" docs/` or `echo "run gh pr create"`. Consolidated into a single `pre-pr-checks.sh` that (1) short-circuits in <5 ms for commands that don't mention the gate keywords, (2) shlex-tokenises the command and splits on shell chain operators (`&&`, `||`, `;`, `|`) so only real `git push` / `gh pr create` invocations trigger, and (3) runs Ruff + svelte-check + template-guard in one hook invocation. `pre-pr-template-guard.sh` now resolves its CWD via `git rev-parse --show-toplevel`, so it works when called from any subdirectory. `.claude/settings.json` updated to reference the single hook (timeout 180s).
- **Schema drift: `optimizations.improvement_score`** — `models.py` declared the column but no migration ever added it, breaking every new insert with `table has no column named improvement_score`. Added migration `938041e0f3dd` (forward-only idempotent `batch_alter_table`).
- **Schema drift: `global_patterns.id` nullability** — primary key declared non-nullable in `models.py` but on-disk SQLite left it nullable. A stray `NULL` row would bypass ORM lookups and silently break promotion/demotion queries. Migration `e2dbcbacab3a` (forward-only, inspector-guarded).
- **Seed pipeline alignment with text-editor pipeline** — batch seeding (`synthesis_seed` MCP tool, `POST /api/seed`, `SeedModal`) now runs the same conceptual path as `POST /api/optimize`:
  - **Tier fidelity**: resolved routing tier (`passthrough`/`sampling`/`internal`) is threaded through `run_batch` → `run_single_prompt` → `PendingOptimization.routing_tier`, replacing the hardcoded `"internal"` literal at `batch_pipeline.py:451`. Tier-aware analytics, cost attribution, and per-tier debugging now see seed rows correctly.
  - **Unified enrichment**: `ContextEnrichmentService.enrich()` is the single entry point for codebase context, strategy intelligence, pattern injection, and divergence detection. Seed path now receives the B0 repo relevance gate (cosine floor + domain entity overlap) and B1/B2 prompt-context divergence alerts. Enrichment-profile selection (`code_aware`/`knowledge_work`/`cold_start`) applies to seeded prompts too. Previously-hardcoded `divergence_alerts=None` replaced with live enrichment output.
  - **Per-prompt SSE events**: `bulk_persist()` emits an `optimization_created` event for every row it actually inserts, carrying `routing_tier`, `batch_id`, and `source="batch_seed"` so consumers can distinguish seed-origin rows. Frontend history refresh and cross-process MCP bridge fire reliably; batch-level `seed_*` events are retained unchanged.
  - **Classification agreement tracking**: each seeded prompt records a heuristic-vs-LLM pair into the `ClassificationAgreement` singleton so `/api/health` agreement + strategy-intel-hit-rate counters reflect seed traffic.
  - **Wiring**: `POST /api/seed` pulls `request.app.state.context_service`; the MCP `synthesis_seed` tool resolves via `tools/_shared.get_context_service()`.

### Removed
- **12 legacy 301/307 redirects** — `/api/taxonomy/{tree,stats,node/{id},recluster}` and `/api/patterns/{families,families/{id},match,graph,stats,search}` handlers deleted from `clusters.py`. Pre-1.0 solo-dev project — no external consumers to preserve. ~90 LOC removed including 10 paired redirect tests in `test_clusters_router.py::TestLegacyRedirects`. `RedirectResponse` import dropped.
- **`OptimizerInput` + `ResolvedContext` Pydantic classes** — orphan dataclasses in `schemas/pipeline_contracts.py` that were never instantiated in production, only in `test_contracts.py`. Paired test classes deleted alongside.
- **`context_resolver.py` service** (~150 LOC) — superseded by `context_enrichment.py`; only caller was its own test. Paired `test_context_resolver.py` deleted.
- **`AdaptationTracker.render_adaptation_state()` method** — never called in production. Class itself preserved (correctly names its feedback-tracking responsibility). Paired `prompts/adaptation.md` template deleted and manifest entry removed.

## v0.3.39 — 2026-04-18

### Added
- **Template entity** — new `prompt_templates` table holds immutable frozen snapshots with full provenance (`source_cluster_id`, `source_optimization_id`, `project_id`, `label`, `prompt`, `strategy`, `score`, `pattern_ids`, `domain_label`, `promoted_at`, `retired_at`, `retired_reason`, `usage_count`, `last_used_at`). Denormalized `template_count` counter on `PromptCluster` for warm-phase reconciliation.
- **Templates router** — `GET /api/templates` (paginated, project-scoped), `GET /api/templates/{id}`, `POST /api/clusters/{id}/fork-template`, `POST /api/templates/{id}/retire`, `POST /api/templates/{id}/use` (rate-limited 30/min). Full Pydantic schemas in `schemas/templates.py`.
- **`TemplateService`** (`services/template_service.py`) — `fork_from_cluster()` with idempotency guard (no re-fork until new top optimization surpasses score threshold), `retire()`, `increment_usage()`, `get()`, `list_for_project()` with pagination, `auto_retire_for_degraded_source()`.
- **`root_domain_label()` pure helper** (`services/taxonomy/domain_walk.py`) — cycle-guarded parent walk (8-hop cap) shared by sub-domain color resolution and template provenance display. 10 unit tests.
- **Frontend `templatesStore`** (`stores/templates.svelte.ts`) — `load()`, `spawn()`, `retire()`, invalidation on `taxonomy_changed` SSE.
- **Halo rendering on 3D topology** — clusters with `template_count > 0` get a 1px contour ring billboard via a growable mesh pool (50→500). `SceneNode.template_count` decorates cluster nodes; `HIGHLIGHT_COLOR_HEX` is an explicit constant, no longer derived from the removed `stateColor('template')`.
- **Inspector templates section** — collapsible "PROVEN TEMPLATES" group with reparent annotation, replacing the previous `state='template'` machinery.
- **`ClusterNavigator` templates group** — PROVEN TEMPLATES section reads from `templatesStore`, grouped by frozen domain label captured at fork time.
- **`ActivityPanel` legacy state rendering** — historical `state='template'` values in the taxonomy activity log render verbatim (unmapped), preserving audit trail readability after the enum change.
- **CI grep guard** — `.claude/hooks/pre-pr-template-guard.sh` blocks residual `state='template'` literals in source files. Exit code `2` fails the pre-PR hook.
- **Readiness sparkline window selector** — `DomainReadinessSparkline` accepts `window: '24h' | '7d' | '30d'` (default `24h`). `TopologyInfoPanel` renders a shared 3-button radiogroup driving both the consistency and gap-to-threshold trendlines so they share an x-axis scale. Wires through to `GET /api/domains/{id}/readiness/history?window=...` so the 7d/30d hourly-bucketed backend bucketing is no longer dead code.
- **Master readiness mute toggle** — `DomainReadinessPanel` header hosts a bell / bell-off button that flips `domain_readiness_notifications.enabled` globally. Distinct from per-row bells: `muted_domain_ids` survive master-mute toggles intentionally so operators can silence every tier-crossing toast briefly (e.g. during a bulk split) without losing their curated per-domain mute list. 1px-stroke SVG bell matching the per-row icon.
- **Sparkline SSE refresh** — `readinessStore` exposes a new `invalidationEpoch` reactive counter bumped on every `invalidate()` call. `DomainReadinessSparkline` reads the epoch inside its fetch `$effect` so tier-crossing SSE events now refresh the trendline endpoint (previously the summary reports refreshed but `/history` went stale until remount).
- **Readiness window persistence** — new `readinessWindowStore` (`stores/readiness-window.svelte.ts`) persists the time-window selection to `localStorage['synthesis:readiness_window']`, following the `nav_collapse` convention. Invalid/missing values fall back to `'24h'`.

### Changed
- **Templates decoupled from `PromptCluster` lifecycle (fork-on-promotion)** — `PromptLifecycleService.check_promotion()` now mints a new `PromptTemplate` row when a mature cluster reaches fork thresholds (`usage_count >= 3`, `avg_score >= 7.5`) instead of transitioning the cluster to `state='template'`. Source cluster stays at `state='mature'` and keeps learning. Constants renamed: `MATURE_TO_TEMPLATE_USAGE_COUNT` → `FORK_TEMPLATE_USAGE_COUNT`, `MATURE_TO_TEMPLATE_AVG_SCORE` → `FORK_TEMPLATE_AVG_SCORE`. Added `AUTO_RETIRE_SOURCE_FLOOR = 6.0` (1.5-pt hysteresis below fork threshold).
- **Warm Phase 0 reconciliation** — now reconciles `template_count` and auto-retires templates whose source cluster degrades (`avg_score < 6.0`) or is archived. Phase 4 `preferred_strategy` recomputation filter flipped from `state='template'` to `template_count > 0`.
- **`domain_readiness_notifications.enabled` default flipped to `true`** — PR #27 shipped the feature gated off-by-default with no UI toggle, which rendered the entire tier-crossing toast pipeline unreachable in a fresh install. Defaults now mirror the new master mute button semantics: on by default, opt-out via header bell or per-row bells. Regression test added for opted-out users across the default flip.
- **Shared tier color tables extracted** — `stabilityTierVar` / `emergenceTierVar` / `emergenceTierBadge` moved out of `DomainReadinessPanel.svelte` into `readiness-tier.ts` next to `TIER_COLORS` so semantic-brand changes now touch a single file.
- **`DomainReadinessPanel` per-row mute** renders a 1px-stroke inline SVG bell (overlay diagonal slash when muted) instead of the 🔔/🔕 emoji pair. Matches the brand spec's `currentColor`-inheriting, zero-glow SVG contour convention.

### Fixed
- **Template spheres inherit domain color instead of neon cyan override** — `stateNodeColor()` used to force every `state='template'` sphere to `stateColor('template')` (#00e5ff), which hid the template's domain membership entirely. Templates now inherit their domain color via `taxonomyColor()`. The sub-domain→parent walk in `buildSceneData` was broadened from `isSubDomain`-only to any node whose `parent_id` is a domain node, so a template under `security > token-ops` now renders in security red (`#ff2255`) instead of cyan or the token-ops OKLab variant.
- **Template cluster visibility in non-template state filters** — template clusters rendered in the "active" tab as a labeled-less cyan sphere whose hover chip fell back to the domain name because `stateOpacity()` ghosted non-matching states to 0.25, which tripped the `nodeOpacity < 0.5` branch that blanks `SceneNode.label`. Templates are now architecturally structural (joining `domain`/`project`) and stay at 0.5 in filtered tabs so the label and hover chip render correctly.
- **Pattern graph sub-domain color parity** — sub-domain nodes in `SemanticTopology` used to resolve colors via their own label (e.g. `token-ops` → OKLab variant `#d20033`) instead of inheriting the parent domain's canonical brand color (`security` → `#ff2255`). `TopologyData.buildSceneData()` now walks up the `state="domain"` parent chain via `rootDomainLabel()` and resolves color against the top-level domain's label. `ClusterNavigator.svelte` `SubDomainGroup` extended with `parentLabel` so the sub-domain row's color dot matches the pattern graph's sphere.
- **`PATCH /api/preferences` 422 on readiness toggles** — the router's `PreferencesUpdate` Pydantic model sets `extra="forbid"` but never declared the `domain_readiness_notifications` key shipped in PR #27, so every per-row mute and the new master-bell click returned 422 before reaching the service layer. Fix adds a strict `_DomainReadinessNotificationsUpdate` sub-model (`enabled: StrictBool | None`, `muted_domain_ids: list[str] | None`, `extra="forbid"` for sub-forbidding). 7 regression tests.
- **`AUTO_RETIRE_SOURCE_FLOOR` circular import** — `warm_phases.py` imported the constant from `prompt_lifecycle.py`, which in turn imported from `taxonomy._constants`, which triggered `taxonomy/__init__.py` → back into `warm_phases` on cold start. `EXCLUDED_STRUCTURAL_STATES` import deferred to the two call sites in `prompt_lifecycle.py`; `AUTO_RETIRE_SOURCE_FLOOR` stays defined there as the canonical source.
- **410-Gone `/api/clusters/templates` rate limit** — the deprecated endpoint body shrank to a single `raise HTTPException(410)` during the sweep but also dropped its `RateLimit` dependency. Restored so the endpoint cannot be cheaply spammed and the audit test passes.

### Removed
- `state='template'` from the cluster lifecycle state enum (Literal widened on read-side for historical rows only).
- `GET /api/clusters/templates` endpoint (returns 410 Gone; `RateLimit` dependency retained).
- `PATCH /api/clusters/{id}/state` with `state='template'` (returns 400 Bad Request).
- `clustersStore.spawnTemplate()` method — superseded by `templatesStore.spawn()` routing through `POST /api/clusters/{id}/fork-template`.

### Migration
Forward-only migration `f1e2d3c4b5a6_template_entity.py` creates `prompt_templates` and adds `template_count` to `prompt_cluster`. Downgrade raises `NotImplementedError` — restore from pre-migration DB backup if revert needed. The migration is idempotent and re-runnable. See `docs/superpowers/specs/2026-04-18-template-architecture-design.md`.

## v0.3.38 — 2026-04-17

### Added
- **Readiness snapshot writer (observability infra)** — new `services/taxonomy/readiness_history.py` with fire-and-forget `record_snapshot(report)` that appends one JSON row per warm-cycle observation to `data/readiness_history/snapshots-YYYY-MM-DD.jsonl`. Daily UTC rotation, sync `Path.open("a")` + `asyncio.to_thread` (mirrors `event_logger.py` pattern, no `aiofiles` dependency). OSError swallowed and logged so warm-path Phase 5 never blocks on disk I/O. New Pydantic models `ReadinessSnapshot`, `ReadinessHistoryPoint`, `ReadinessHistoryResponse` in `schemas/sub_domain_readiness.py`; `ReadinessSnapshot.ts` has a `field_validator` that normalizes naive → UTC and converts aware datetimes so rotation never drifts across timezones. Retention/bucket constants (`READINESS_HISTORY_RETENTION_DAYS=30`, `READINESS_HISTORY_BUCKET_THRESHOLD_DAYS=7`) added to `_constants.py` for the upcoming history endpoint
- Readiness time-series: per-domain JSONL snapshots written every warm-path Phase 5, retained 30 days.
- `GET /api/domains/{id}/readiness/history?window=24h|7d|30d` — windowed trajectory with hourly bucket means for windows ≥ 7d.
- `DomainReadinessSparkline` peer component rendered from `TopologyInfoPanel` — 24h consistency sparkline beside `DomainStabilityMeter` and 24h gap-to-threshold trendline beside `SubDomainEmergenceList`. Existing meter Props contracts unchanged.
- Tier-crossing detector for domain readiness with 2-cycle hysteresis and per-domain cooldown — oscillations within the pending streak reset the counter so a single stray observation never fires a transition.
- `domain_readiness_changed` SSE event published on confirmed tier transitions across both axes (stability `healthy`↔`guarded`↔`critical`, emergence `inert`↔`warming`↔`ready`). Stable wire shape documented as `DomainReadinessChangedEvent`: 9 fields including `axis`, `from_tier`, `to_tier`, `consistency`, `gap_to_threshold`, `would_dissolve`, and ISO-8601 `ts`.
- `domain_readiness_notifications` preference with `enabled` gate (default off) and `muted_domain_ids[]` per-domain mute list. Validate + sanitize accept only `bool` / `list[str]` shapes; corrupt entries are replaced with defaults instead of rejected.
- `toastStore.info()` variant for informational (cyan) toasts with an optional `dismissMs` override, complementing the existing `success` / `warning` / `error` / `add()` API.
- Readiness-notification SSE dispatcher — surfaces `domain_readiness_changed` events as coloured toasts gated by the preference + per-domain mute list. Severity mapping: `would_dissolve` or stability→critical renders red, stability→guarded renders yellow, everything else routes through the new `info()` variant.
- Per-row mute toggle in `DomainReadinessPanel` — bell / bell-off glyph with `aria-pressed`, accessible `aria-label`, optimistic preference update with inverse-toggle rollback on API failure. Keyboard-navigable without intercepting row-select activation.
- **Readiness topology overlay** — domain nodes in `SemanticTopology` now render a per-domain readiness ring decorated by composite tier (`composeReadinessTier()` priority: `inert` → emergence dominates over stability, otherwise stability → critical/guarded → warming/healthy/ready). `readiness-tier.ts` exposes the `TIER_COLORS` palette (healthy `#16a34a`, warming `#0ea5e9`, guarded `#eab308`, critical `#dc2626`, ready `#f97316`); `SceneNode.readinessTier` is decorated by `buildSceneData()` from `readinessStore.byDomain()`. Rings live in a dedicated `THREE.Group` (mirrors `beamPool.group`) so they survive `rebuildScene()`'s scene-clear traverse; each ring billboard-orients to camera per frame and tier transitions tween via cubic-bezier color interpolation (`prefersReducedMotion()` aware, `requestAnimationFrame` driven, `TweenHandle.cancel()` prevents RAF use-after-free on supersede or unmount). LOD opacity attenuation reads `renderer.lodTier` each frame and composes `lodFactor × READINESS_RING_OPACITY_FACTOR × node.opacity × dimFactor` (where `dimFactor = DOMAIN_DIM_FACTOR (0.15)` for non-highlighted domains when a highlight is active, else `1.0`). Geometry rebuilds when `node.size` changes (cluster growth) via shared `buildRingGeometry(size)` helper; ring registry deduplicated dispose lifecycle via `disposeRingEntry(entry)` shared between rebuild-pruning and unmount-cleanup paths. Reactive chain: `readinessStore.invalidate()` already runs on `taxonomy_changed` / `domain_created` / `domain_readiness_changed` SSE → `buildSceneData()` re-decorates `SceneNode.readinessTier` → `{#each}` block flips the `data-readiness-ring` / `data-readiness-tier` markers and the ring-build pass tweens to the new color. Brand-compliant: 1px contour, `transparent: true` + `depthWrite: false`, no glow / shadow / emissive / bloom (assertion-locked via brand-guard test). 23 frontend tests across `readiness-tier.test.ts`, `TopologyData.test.ts`, and `SemanticTopology.test.ts` cover priority rules, decoration, marker presence + reactivity, billboard per-frame, dim sweep, tween cancel-on-unmount, snap-back protection on rapid tier changes, geometry rebuild on size change, LOD attenuation across far/mid/near, dim×LOD composition, and brand directive compliance. Full suite: 1142/1142 passing

### Changed
- `DomainReadinessPanel` rows migrated from `<button>` to `<div role="button" tabindex="0">` so the nested mute button no longer violates the no-nested-interactive-element HTML rule. Keyboard handling: Enter activates without `preventDefault` (preserves native form semantics), Space activates with `preventDefault` (blocks page scroll). Child `aria-pressed` button stops propagation so toggling mute never fires row selection.
- Review follow-ups for the readiness bundle (PR #27): snapshot retention cutoff now day-aligned UTC (boundary files kept per docstring contract), `asyncio.gather` fire-and-forget on per-cycle snapshot writes with `return_exceptions=True` (one slow domain no longer blocks the batch), `ValidationError` added to the swallowed exception set, keyboard propagation guard on `DomainReadinessPanel` rows (Space/Enter on the nested mute button no longer fires row selection), cooldown-gated crossings now emit a structured `readiness_crossing_suppressed` observability event so suppressed transitions remain diagnosable, malformed SSE payloads in `dispatchReadinessCrossing` leave a `console.debug` crumb, topology `buildSceneData` drops readiness tiers that aren't in the frontend's `ReadinessTier` enum (schema-drift guard against future backend tier additions), `updateRingFrameInputs(entry, node)` helper extracted so the two sites keeping LOD-input fields fresh can't drift apart, and assorted clarifying comments on the LOD RAF callback, ring-group unmount, and `buildRingGeometry` camera-asymmetry rationale.

## v0.3.37 — 2026-04-17

### Added
- **Domain & sub-domain readiness endpoints** — `GET /api/domains/readiness` (batch, sorted critical→healthy then by emergence gap) and `GET /api/domains/{id}/readiness` (single). Exposes the live three-source qualifier cascade (domain_raw > intent_label > tf_idf), adaptive threshold `max(0.40, 0.60 − 0.004 × total_opts)`, dissolution 5-guard evaluation, and 30s TTL cache keyed by `(domain_id, member_count)` — new optimizations naturally invalidate stale entries. Debounced `readiness/sub_domain_readiness_computed` + `readiness/domain_stability_computed` taxonomy events (5s per domain). `?fresh=true` bypasses cache for live recomputation. Standalone `sub_domain_readiness.py` service module so analytics never mutate engine state. New Pydantic schema file `schemas/sub_domain_readiness.py` (`DomainReadinessReport`, `DomainStabilityReport`, `SubDomainEmergenceReport`, `QualifierCandidate`, `DomainStabilityGuards`). 22 unit tests in `test_sub_domain_readiness.py`, full taxonomy suite still green
- **Readiness UI surface** — `DomainStabilityMeter.svelte` (1px-contoured consistency gauge with dissolution-floor + hysteresis markers, ARIA `role="meter"`, chromatic tier encoding: green=healthy, yellow=guarded, red=critical, failing-guard chips when dissolution imminent), `SubDomainEmergenceList.svelte` (top qualifier card with per-row threshold-relative gauge, source badges RAW/INT/TFI, runner-up rows, empty-state copy per blocked reason), `DomainReadinessPanel.svelte` (global sidebar listing sorted critical→guarded→healthy then by emergence proximity, click-through `domain:select` CustomEvent). Integrated into `TopologyInfoPanel.svelte` domain mode. `readinessStore` with 30s stale window matching backend TTL, invalidated on `taxonomy_changed`/`domain_created` SSE. `ActivityPanel` recognizes new `readiness` op. 16 Vitest tests verifying tier colors, ARIA contract, sort order, `domain:select` dispatch, and zero `box-shadow`/`text-shadow`/`drop-shadow` regressions (brand compliance guard)
- **Enriched qualifier vocabulary generation** — `generate_qualifier_vocabulary()` now receives per-cluster centroid cosine similarity matrix + intent labels + domain_raw qualifier distribution as structured context for Haiku (new `ClusterVocabContext` dataclass). Similarity thresholds `_VOCAB_SIM_HIGH=0.7` / `_VOCAB_SIM_LOW=0.3` render the matrix as "very similar" / "distinct" pairs in the prompt. Unknown matrix cells use `None` (not `0.0`) so Haiku doesn't mistake missing geometry for orthogonality. Post-generation quality metric (0–1 scale, capped at 500 cluster pairs) emitted via new `vocab_generated_enriched` event with `matrix_coverage_pct`, `clusters_with_intents`, `quality_score`, and per-stage timings. `avg_vocab_quality` exposed in health endpoint's `qualifier_vocab` stats. Qualifier case + whitespace normalized at write time
- **Unified collapsible navigator sections** — new `CollapsibleSectionHeader.svelte` primitive (whole-bar + split modes, Snippet-based slots) and `navCollapse` store (`localStorage` key `synthesis:navigator_collapsed`, default-open policy) replace ad-hoc chrome for DOMAIN READINESS, PROVEN TEMPLATES, and per-domain groups in `ClusterNavigator.svelte`. Consistent 20px bars, Syne-uppercase labels, ▾/▸ caret character swap (no rotation/glow per brand spec), 1px contours only. Split-mode per-domain header preserves dual action — caret toggles collapse, label toggles topology highlight via `event.stopPropagation()`. Sub-domain collapse state migrated from local `Set` to shared store (persists across refreshes). 10 Vitest tests cover whole-bar/actions/split modes, ARIA contract, stop-propagation boundaries, and `assertNoGlowShadow()` brand guard

### Changed
- **Shared qualifier cascade primitive** — `engine._propose_sub_domains()` now consumes the pure `compute_qualifier_cascade()` function in `sub_domain_readiness.py` instead of duplicating the three-source cascade (Source 1 `domain_raw` → Source 2 `intent_label` → Source 3 `raw_prompt` × dynamic TF-IDF) inline. Eliminates drift between sub-domain creation and the `/api/domains/readiness` endpoint by construction — both consumers now see the exact same tallies. Source-key naming standardized on `"tf_idf"` (was `"dynamic"`) in the `sub_domain_signal_scan` observability event payload. `_reevaluate_sub_domains()` retains its inline narrow single-qualifier matcher (different semantics: no vocab gate on Source 1, per-sub `sub_keywords` on Source 2). No behavioral change for discovery; promotion gating (MIN_MEMBERS, adaptive threshold, dedup, `dissolved_this_cycle` guard, domain ceiling, event emission) preserved verbatim
- **Default Opus model bumped to 4.7** — `MODEL_OPUS` default in `config.py` updated from `claude-opus-4-6` to `claude-opus-4-7` (canonical API ID per Anthropic docs). Opus 4.7 ships with a native 1M-token context window at standard pricing (no beta header required), adaptive thinking, 128k max output, and prompt caching. `.env.example`, `backend/CLAUDE.md`, `release.sh` Co-Authored-By trailer, `pipeline_constants.compute_optimize_max_tokens` comment, and all hardcoded test references updated. No code path change — same pricing tier, same `thinking_config()` branch, streaming optimize/refine phases continue to use the 128K output budget
- **Mypy strict-mode cleanup (103 → 0 errors across 133 backend source files)** — `backend/app/models.py` refactored to SQLAlchemy 2.0 `Mapped[]` typed declarative columns (~580-line rewrite) so ORM attribute types are introspectable. New `[tool.mypy]` section in `backend/pyproject.toml`. Pattern adopted throughout: `# type: ignore[assignment]` on Pydantic `Literal` field assignments where runtime values are validated, `# type: ignore[arg-type]` on `np.frombuffer(bytes | None)` sites, `Any`-typed fields for UMAP / httpx / embedding-index optional deps

### Fixed
- **`release.sh` correctness and safety** — (1) CHANGELOG migration: script now moves items from `## Unreleased` into a new `## vX.Y.Z — YYYY-MM-DD` section at release time (previously documented but never implemented). Idempotent with empty-Unreleased fallback. (2) Dry-run works with a dirty tree. (3) `gh auth status` verified during preflight — previously only `command -v gh` was checked. (4) `ERR` trap with per-step tracking (`CURRENT_STEP`) surfaces exactly which step failed and prints recovery commands. (5) Remote-sync check refuses to release if local main is behind `origin/main`. (6) Smart `--latest` detection via `sort -V`. (7) Semver validation on the release version string. Dev-bump preserves/seeds an empty `## Unreleased` header after migration
- **Sub-domain measurement-drift flip-flop** — `_reevaluate_sub_domains()` now uses the full three-source cascade (Source 1 `domain_raw` parse + Source 2 `intent_label` vs organic vocab + Source 3 `raw_prompt` × dynamic `signal_keywords` with weight-gated `_min_hits`) matching `_propose_sub_domains()`. Previously reeval measured only Sources 1+2, so sub-domains created via Source 3 TF-IDF matches were invisible on re-evaluation, causing dissolve/recreate oscillation. Regression test `test_source3_dynamic_keyword_parity_prevents_drift` added
- **Markdown renderer drops content after pseudo-XML wrappers** — `MarkdownRenderer.svelte` now sanitizes optimizer pseudo-XML tags (`<context>`, `<requirements>`, `<constraints>`, `<instructions>`, `<deliverables>`, etc.) before passing content to `marked`. Per CommonMark, an unknown opening tag alone on a line starts an HTML block (type 7), which suppresses markdown parsing on the immediately following line — causing the first paragraph inside each wrapper to render as literal `**text**` instead of `<strong>text</strong>`. The sanitizer strips block-level pseudo-XML and escapes inline pseudo-XML while preserving the HTML5 element whitelist. Global fix — applies to every `MarkdownRenderer` consumer (ForgeArtifact, Inspector). 5 regression tests added
- **Phase 4.5 global-pattern sub-step isolation** — the three sub-steps (`_discover_promotion_candidates`, `_validate_existing_patterns`, `_enforce_retention_cap`) are now each wrapped in their own `async with db.begin_nested()` SAVEPOINT, so a transient failure in one step no longer poisons the whole maintenance transaction. Failure logs now surface `exc.orig` / `exc.__cause__` as `root_cause` for faster triage
- **Vocab generation session poisoning** — new Phase 4.95 runs `_propose_sub_domains(vocab_only=True)` in an isolated DB session so a stale vocabulary read cannot short-circuit the rest of Phase 5. `family_ops.merge_meta_pattern` is wrapped in a SAVEPOINT so meta-pattern merge failures during vocab pass don't cascade. Vocab enrichment observability hardened: 0.1–0.3 quality band now emits a WARN (was silent), and fallback reasons are differentiated (`query_failed` / `matrix_failed` / `no_centroids`) instead of a single generic path
- **hnswlib SIGILL on Python 3.14** — subprocess probe pattern extended to all HNSW-dependent test files (`test_backend_benchmark.py`, `test_hnsw_backend.py`, `test_backend_project_filter.py`) so CI on Python 3.14 skips instead of crashing the worker
- **FastAPI `Request` param regression** — three `github_repos.py` routes (`/tree`, `/files/{path}`, `/branches`) had `request: Request | None = None` from the mypy cleanup pass; FastAPI rejects `Request | None` at registration time with `FastAPIError: Invalid args for response field`. Restored to `request: Request` as the first non-path parameter (FastAPI auto-injects). Verified `./init.sh restart` → all three services healthy

## v0.3.36 — 2026-04-16

### Added
- **Qualifier-augmented embeddings** — 4th embedding signal (`qualifier_embedding`) from organic Haiku-generated vocabulary. Qualifier keywords embedded as 384-dim vector via `all-MiniLM-L6-v2`, stored per optimization. `QualifierIndex` (same pattern as `TransformationIndex`) tracks per-cluster mean qualifier vectors. Qualifier embedding cache on `DomainSignalLoader` eliminates repeated MiniLM calls for identical keyword sets. Phase 4 backfill (capped 50/cycle) for existing optimizations
- **5-signal fusion pipeline** — `PhaseWeights` and `CompositeQuery` extended from 4 to 5 signals. `_DEFAULT_PROFILES` and `_TASK_TYPE_WEIGHT_BIAS` updated with per-phase qualifier weights. `compute_score_correlated_target()` skips qualifier dimension for old profiles (`w_qualifier=0.0`) to prevent cold-start bias
- **Domain dissolution** — `_reevaluate_domains()` evaluates top-level domains with 5 guards: "general" permanent, sub-domain anchor (bottom-up only), age ≥48h, member count ≤5, consistency <15% (Source 1 only). Shared `_dissolve_node()` extracts dissolution logic for both domain and sub-domain paths. Dissolution reparents clusters to "general", merges meta-patterns (not deletes), clears resolver + signal loader
- **`DomainSignalLoader.remove_domain()`** — clears signals, patterns, qualifier cache, and embedding cache for a dissolved domain. Called by `_dissolve_node()` with `clear_signal_loader=True` for domain-level dissolution
- **Domain lifecycle health stats** — `domain_lifecycle` field in health endpoint: `domains_reevaluated`, `domains_dissolved`, `dissolution_blocked`, `last_domain_reeval`
- **Phase 5 execution reorder** — sub-domain re-evaluation → domain re-evaluation → domain discovery → sub-domain discovery → existing post-discovery ops. Bottom-up dependency ensures sub-domains dissolve before parent domains
- **Cross-sub-domain merge observability** — `merge/cross_sub_domain` event logged when merge winner and loser are in different sub-domains

### Changed
- **Blend weights** — `CLUSTERING_BLEND_W_RAW` reduced from 0.65 → 0.55. New `CLUSTERING_BLEND_W_QUALIFIER = 0.10`. Total still 1.0 (0.55/0.20/0.15/0.10)
- **`blend_embeddings()` signature** — `qualifier` added as keyword-only parameter (after `*`). Existing positional callers unaffected
- **`PhaseWeights.from_dict()` default** — `w_qualifier` defaults to 0.0 (not 0.25) for backward compat with old 4-element profiles
- **1:1 vocabulary coverage** — `generate_qualifier_vocabulary()` minimum lowered from 3 to 2 clusters. Vocabulary generation decoupled into separate all-domains pass (including "general"). All non-empty domains now have organic vocabulary
- **Sub-domain re-evaluation** — uses three-source cascade (domain_raw + intent_label + TF-IDF) instead of Source 1 only. Prevents false dissolutions when organic vocab uses different qualifier names than old static vocabulary
- **Phase 5.5 meta-pattern handling** — changed from DELETE to UPDATE (merge into parent domain), consistent with Phase 5 dissolution
- **Backend test count** — 2223 tests (up from 2213)

### Fixed
- **HNSW segfault on Python 3.14** — hnswlib probe uses subprocess to detect SIGILL crash safely. `EmbeddingIndex.rebuild()` catches HNSW build failures and falls back to numpy. HNSW-dependent tests skip on non-functional platforms
- **Phase 5.5 missing `await`** — 4 async `index.remove()` calls in `phase_archive_empty_sub_domains()` were never awaited, causing stale vectors to persist in live indices
- **`_optimized_index` attribute name** — `_dissolve_node()` and Phase 5.5 correctly reference private `_optimized_index` (no public property exists). Pre-existing bug silently caught by `AttributeError` handler
- **Sub-domain flip-flop** — `dissolved_this_cycle` set blocks same-cycle re-creation. Three-source cascade in re-evaluation prevents false dissolution from vocabulary name drift
- **Cold path `w_raw` formula** — now subtracts `CLUSTERING_BLEND_W_QUALIFIER` to maintain correct proportions during adaptive downweighting
- **Split path qualifier** — `split_cluster()` now passes `qualifier_embedding` to `blend_embeddings()` and includes it in the split cache query
- **6 missing `qualifier_index.remove()` calls** — all cluster lifecycle operations (merge, retire, dissolve, archive) now clean up the qualifier index

### Removed
- **Seed domain protection** — `source="seed"` checks removed from `_reevaluate_sub_domains()`, `phase_archive_empty_sub_domains()`, `_suggest_domain_archival()`, `_check_signal_staleness()`. Seed domains subject to same organic lifecycle per ADR-006

## v0.3.35 — 2026-04-15

### Added
- **Warm path maintenance decoupling** — split `execute_warm_path()` into lifecycle group (Phases 0–4, dirty-cluster-gated) and maintenance group (Phases 5–6, cadence-gated). Maintenance phases run every `MAINTENANCE_CYCLE_INTERVAL` (6) warm cycles (~30 min) or immediately when retrying after transient failure via `_maintenance_pending` flag. Fixes Phase 5 (discovery) being silently skipped when no dirty clusters exist
- **Fully organic sub-domain vocabulary** — deleted static `_DOMAIN_QUALIFIERS` dict (9 domains, ~80 curated keywords). All domains now get Haiku-generated vocabulary from cluster labels via `generate_qualifier_vocabulary()`. Vocabulary cached in `cluster_metadata["generated_qualifiers"]`, served to hot path via `DomainSignalLoader.get_qualifiers()`. Cross-process coherence: `load()` populates qualifier cache from DB for MCP server
- **Sub-domain lifecycle management** — removed permanent discovery lock (`if existing_sub_count > 0: continue`). Domains with existing sub-domains are now re-evaluated every Phase 5 cycle. New sub-domains form alongside existing ones. `_reevaluate_sub_domains()` dissolves sub-domains with qualifier consistency below 25% (hysteresis: creation at 40–60%, dissolution at 25%). Dissolution reparents clusters to top-level domain, merges meta-patterns into parent (not deleted), frees label for future re-discovery
- **Sub-domain flip-flop prevention** — `dissolved_this_cycle` set blocks same-cycle re-creation of dissolved labels. Labels freed for future cycles only
- **Shared qualifier matching utility** — `DomainSignalLoader.find_best_qualifier()` static method eliminates duplicate keyword-hit logic between `_enrich_domain_qualifier()` and engine.py Source 2
- **Health endpoint qualifier stats** — `qualifier_vocab` field with `qualifier_cache_hits/misses`, `domains_with_vocab`, `last_qualifier_refresh`
- **DomainResolver.remove_label()** — clears dissolved sub-domain labels from resolver cache to prevent stale resolution
- **Cross-sub-domain merge observability** — `merge/cross_sub_domain` event logged when merge winner and loser are in different sub-domains
- **HNSW fallback resilience** — `EmbeddingIndex.rebuild()` catches HNSW backend build failures and falls back to numpy. HNSW tests skip on platforms where hnswlib is non-functional (Python 3.14)

### Changed
- **Enrichment threshold** — `SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS` lowered from 2 to 1. Domain is already confirmed by classification — single keyword hit is sufficient for qualifier selection
- **Child scan expansion** — `_propose_sub_domains()` scans clusters under existing sub-domains (not just direct domain children). Fixes qualifier counts missing optimizations reparented under sub-domains
- **Phase 5.5 meta-pattern handling** — changed from DELETE to UPDATE (merge into parent domain), consistent with Phase 5 dissolution and preventing `OptimizationPattern` FK orphaning
- **Warm path module docstring** — updated to document lifecycle vs maintenance group architecture
- **`sub_domain_signal_scan` event** — gains `vocab_source: "organic"` field in context
- **Backend test count** — 2201 tests (up from 2177)

### Fixed
- **Phase 5 skipped on idle warm cycles** — the dirty-cluster early-exit gate (`warm_path.py:428`) was too aggressive, skipping maintenance phases even when no dirty clusters existed. Phase 5 now runs independently via cadence gate
- **Phase 5 transient failure not retried** — SQLite `database is locked` during sub-domain creation caused Phase 5 to fail silently with no retry on subsequent cycles. `_maintenance_pending` flag now triggers immediate retry
- **Multi-word dynamic keyword normalization** — `known_qualifiers` stored "api gateway" but Source 1 validation checked "api-gateway". Both forms now stored for consistent matching
- **`SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS` dead code** — constant was defined but never imported. Now used in both `_enrich_domain_qualifier()` and engine.py Source 2
- **Stale "static vocab" references** — replaced all `_DOMAIN_QUALIFIERS`, `has_static_vocab`, and "static vocabulary" references with organic vocabulary terminology

### Removed
- **`_DOMAIN_QUALIFIERS` static dict** — 48 lines of curated keyword groups across 9 domains. Replaced by fully organic Haiku-generated vocabulary
- **Permanent sub-domain discovery lock** — `if existing_sub_count > 0: continue` guard that prevented new sub-domains from forming alongside existing ones
- **`sub_domain_domain_skipped` event** — replaced by `sub_domain_domain_reevaluated` (domains with existing sub-domains are now re-evaluated, not skipped)

## v0.3.34 — 2026-04-15

### Added
- **Signal-driven sub-domain discovery** — replaced HDBSCAN-based sub-domain discovery with a deterministic three-source qualifier pipeline: (1) domain_raw sub-qualifiers via `parse_domain()`, (2) intent_label matching against qualifier vocabulary, (3) raw_prompt matching against dynamic TF-IDF signal_keywords. Adaptive consistency threshold `max(40%, 60% - 0.4% * members)`, minimum 2-cluster breadth guard, full observability with 8 event types per warm cycle
- **Qualifier enrichment** — heuristic analyzer enriches domain classification with sub-qualifiers via `_enrich_domain_qualifier()`. Runs on every prompt at zero LLM cost
- **LLM-generated qualifier vocabulary** — Haiku analyzes a domain's cluster labels and generates qualifier keyword groups. Cached in `cluster_metadata["generated_qualifiers"]`, refreshed when cluster count changes by ≥30%. One LLM call per domain, not per optimization. (v0.3.35: became the sole vocabulary source — static `_DOMAIN_QUALIFIERS` removed)
- **Sub-domain archival phase** (Phase 5.5) — garbage-collects empty and single-child sub-domains after 1h grace period. Reparents children to top-level domain before archiving. Runs after Phase 5 discovery
- **Sub-domain color derivation** — sub-domain nodes derive color from parent domain (same hue, darker in OKLab). Parent color auto-assigned if NULL at creation time
- **Tree integrity check 8** — detects empty sub-domain nodes as violations, logged as errors for observability
- **Repo relevance gate (hybrid)** — two-tier gate prevents same-stack-different-project codebase contamination: cosine floor (`REPO_RELEVANCE_FLOOR = 0.20`) + domain entity overlap via `extract_domain_vocab()`. Tracked in `enrichment_meta`
- **Architecture reference** — `docs/architecture/sub-domain-discovery.md` covering three-source pipeline, vocabulary tiers, adaptive threshold, readiness dashboard, and lifecycle
- **Taxonomy Observatory roadmap entry** — vision for live domain/sub-domain lifecycle dashboard with readiness indicators, dynamic steering, and vocabulary transparency

### Changed
- **Health endpoint** — MCP probe skipped when no active session (eliminates 400 noise). MCP-down produces degraded (200) not unhealthy (503). Only critical services yield 503
- **EmbeddingIndex MCP refresh** — mtime-based change detection replaces age-based staleness. Eliminates "cache stale" log spam when no sessions are active
- **SSE shutdown** — CancelledError handled in generator, drain time increased from 0.1s to 0.5s for clean shutdown
- **Cold path sub-domain preservation** — Step 12 preserves sub-domain parent links instead of flattening all clusters to top-level domain
- **Phase 0 UMAP reconciliation** — separate loop covers all domain nodes including sub-domains. Two-strategy lookup (domain field → parent_id fallback) for sub-domain UMAP positioning
- **ACT filter** — shows all living states (active + mature + template + candidate), not just literal `state="active"`
- **Template visibility** — templates appear in both PROVEN TEMPLATES section and their domain group in the hierarchy
- **Column headers** — moved below PROVEN TEMPLATES section to align with cluster columns
- **Trace logger** — added optional `status` field ("ok", "error", "skipped") for observability

### Fixed
- **Taxonomy re-parenting** — Phase 0 reconciliation now re-parents clusters whose `domain` field doesn't match their parent domain node. Sub-domain children correctly preserved
- **Sub-domain label mapping** — re-parenting sweep maps sub-domain labels to their parent domain for `cluster.domain`
- **Matching excludes structural nodes** — family-level search filters `EXCLUDED_STRUCTURAL_STATES`
- **Matching includes mature/template states** — fallback queries active, candidate, mature, and template
- **Leaf cluster pattern loading** — patterns loaded directly from leaf nodes
- **Zero-pattern suggestion suppression** — frontend hides banners when matched cluster has no meta-patterns
- **Event logger missing `path=`** — added required parameter to cluster state change endpoint
- **Sub-domain color inconsistency** — 4 backend sub-domains had wrong colors from NULL parent color fallback. Data repaired, code hardened
- **Sub-domain creation churn** — HDBSCAN re-ran every warm cycle creating duplicates with different Haiku labels. Replaced with deterministic signal-driven discovery

### Removed
- Internal plans and spec documents from public repository
- HDBSCAN-based sub-domain discovery (batch_cluster, blend_embeddings, generate_label imports within `_propose_sub_domains`)
- 6 HDBSCAN sub-domain constants (SUB_DOMAIN_MIN_MEMBERS, SUB_DOMAIN_COHERENCE_CEILING, SUB_DOMAIN_MIN_GROUP_MEMBERS, SUB_DOMAIN_HDBSCAN_MIN_CLUSTER, SUB_DOMAIN_MIN_CLUSTERS, SUB_DOMAIN_CLUSTER_PATH_MIN_MEMBERS)

## v0.3.31 — 2026-04-13

### Added
- **Live pattern detection on typing** — two-path detection replaces paste-only system: typing path (800ms debounce, 30-char min) + paste path (300ms, 30-char delta). Patterns now surface as users type, not just on paste. AbortController cancels in-flight requests. Persistent chip bar below textarea confirms applied patterns
- **Proven template promotion system** — backend-validated quality gates (avg_score >= 6.0 + members/usage), `promoted_at` timestamp on all promotions, taxonomy event logging for manual state changes. Warm-path Phase 0 health check: demote templates below score 5.5 or coherence 0.4, archive empty+unused ghosts. Phase 4 recomputes preferred_strategy for templates after mutations
- **Template preview card** — inline expandable card in ClusterNavigator showing pattern texts, best prompt excerpt, score. Two actions: "Load Prompt + Patterns" (full template load) and "Apply Patterns Only" (keep user's prompt, inject template patterns)
- **Post-optimization pattern attribution** — `applied_pattern_texts` stored in `enrichment_meta` across all tiers (passthrough via enrichment, internal/sampling via pipeline). ForgeArtifact renders injected pattern texts with source cluster labels in enrichment section
- **Template Inspector enhancements** — usage stats ("Applied to N optimizations"), pattern effectiveness % (source_count/member_count), demote button for template-state clusters
- **ADR-007: Live Pattern Intelligence** — architecture for 3-tier progressive context awareness during prompt authoring (future: context panel, enrichment preview, proactive hints)
- **Task-type signal extraction** — TF-IDF mining from taxonomy discoveries, wired into lifespan + warm path + MCP. Dynamic `_TASK_TYPE_SIGNALS` with compound keyword preservation
- **Sub-domain meta-pattern aggregation** (Phase 4.25) — rolls up child cluster patterns into sub-domain nodes

### Changed
- **Pattern suggestion banner** — shows pattern text previews (top 3), domain color dot, "Apply N" with count. No auto-dismiss — stays until Apply/Skip/new match
- **Match endpoint hardened** — rate limited (30/min), max_length=8000 on prompt_text
- **Match thresholds recalibrated** — lowered for raw embeddings (family 0.55, cluster 0.45, candidate 0.65). Composite fusion removed from match_prompt() for cross-process consistency between backend and MCP
- **MCP embedding index freshness** — refresh interval reduced from 600s to 30s, event-driven reload on taxonomy_changed for zero-stale guarantee
- **meta_pattern_count** added to ClusterNode in tree endpoint (single GROUP BY query)
- **Passthrough tier** now gets full-quality pattern injection (auto_inject_patterns with composite fusion, cross-cluster, GlobalPattern 1.3x boost) and few-shot examples

### Fixed
- **CRITICAL: Strategy intelligence silently lost** — all templates (optimize.md, passthrough.md, refine.md) used `{{strategy_intelligence}}` but all pipeline render calls passed `"adaptation_state"` key. Strategy performance rankings, anti-patterns, and user feedback were computed but never injected. Only batch_pipeline was correct
- **MCP refine tool missing enrichment layers** — codebase_context and strategy_intelligence not forwarded to create_refinement_turn (REST refine was correct)
- **MCP match returning "none" for valid prompts** — composite fusion used process-local engine state that diverged between backend and MCP. Removed fusion from match_prompt; both processes now produce identical results
- **Usage count inflation** — auto_inject_patterns now returns only cluster IDs that actually contributed patterns, preventing inflation for embedding-matched clusters with no MetaPattern records
- Fixed sub-domain nodes not included in filteredTaxonomyTree
- Fixed sub-domain coherence not set at creation time
- Fixed sub-domain labels stored as parent-prefixed instead of qualifier-only
- Fixed sub-domain patterns not included in injection pipeline
- Fixed domain mapping setting wrong domain on Optimization records

## v0.3.30 — 2026-04-13

### Added
- **Enrichment engine consolidation** — unified `ContextEnrichmentService.enrich()` with auto-selected profiles (code_aware / knowledge_work / cold_start), task-gated curated retrieval, and strategy intelligence merging performance signals + adaptation feedback into a single advisory layer. Replaces 7 scattered context layers with 4 profile-gated active layers
- **Prompt-context divergence detection** — two-layer system detects tech stack conflicts between prompt and linked codebase. Layer 1 (keyword) flags framework/database/language mismatches; Layer 2 (optimizer LLM) classifies intent as OVERSIGHT, DELIBERATE CHANGE, UPGRADE, or STANDALONE. Alerts injected into optimizer template with `{{divergence_alerts}}` variable
- **Heuristic classifier accuracy** — compound keyword signals (A1), technical verb+noun disambiguation (A2), domain signal auto-enrichment via TF-IDF (A3), and confidence-gated Haiku LLM fallback (A4) with `enable_llm_classification_fallback` preference
- **Domain-relaxed fallback queries** — strategy intelligence and anti-pattern queries fall back to task_type-only across all domains when exact domain+task_type returns empty
- **Classification agreement tracking** — compares heuristic vs LLM task_type and domain after every analysis phase. Agreement rates and `strategy_intelligence_hit_rate` exposed in `GET /api/health`
- **Enrichment telemetry panel** — ForgeArtifact ENRICHMENT section with profile, classification, layer activation, strategy rankings, domain signal scores, divergence alerts, disambiguation and LLM fallback indicators
- **Hierarchical edge system** — curved edge bundling in 3D topology with depth-based attenuation, density-adaptive opacity, proximity suppression, focus-reveal on hover, and domain-colored edges
- **Command palette** — wired up with proper business logic for keyboard-driven navigation

### Changed
- **Workspace guidance collapsed into codebase context** — now a fallback within codebase context when explore synthesis is absent
- **History cluster badge** — clickable cluster label for cross-tab navigation to ClusterNavigator
- **TopologyControls ambient badge** — filter-aware count matching current state filter

### Fixed
- Fixed `project_id` not set at creation time across optimization pipelines
- Fixed MCP tool calls not auto-resolving linked repo for codebase context
- Fixed intent label generation leaving parenthetical verb suffix artifacts
- Fixed context injection container brand compliance and data clarity
- Skeleton loading animations replaced gradient shimmer with solid-color opacity pulse (zero-effects compliance)
- Standardized hover transition timing to 200ms across Navigator and Inspector
- Fixed non-standard font weights, hardcoded hex fallbacks, and data row heights across Navigator, ClusterNavigator, ActivityPanel, Inspector
- Removed unused imports and dead code (clusters store, Navigator, SemanticTopology)

## v0.3.29 — 2026-04-11

### Added
- **Injection effectiveness measurement** — warm path Phase 4 now computes mean score lift for pattern-injected vs non-injected optimizations. Logged as `injection_effectiveness` taxonomy event and surfaced in `GET /api/health` response
- **Pattern observability** — Phase 4 refresh logs merged/created/pruned counts per cluster, cross-cluster provenance counted in health stats, pipeline traces include injection details, ActivityPanel displays `global_pattern`, `injection_effectiveness`, and `skip` op types
- **Orphan recovery system** — detects optimizations where hot-path extraction failed (embedding IS NULL), retries with fresh sessions, exponential backoff (3 attempts), and health metrics. Piggybacks on warm-path timer. `recovery` section in `GET /api/health`, `recovery/scan|success|failed` taxonomy events
- **Project node UX** — project nodes render as dodecahedrons (structural geometry), rich hover tooltip showing domain/cluster counts, inspector project mode with DOMAINS/CLUSTERS/OPTS/SCORE metrics and domain composition bar, sidebar groups projects separately from domain clusters

### Changed
- **GlobalPattern promotion unlocked for single-project** — removed hard `MIN_PROJECTS=2` gate that blocked all global pattern promotion. Cluster breadth (≥5 clusters) is now the sole quality gate; cross-project count remains as an observability metric
- **Phase 4 refresh preserves pattern history** — replaced delete-all-then-recreate with incremental merge + excess pruning (`MAX_PATTERNS_PER_CLUSTER=15`). `source_count` now accumulates organically across refresh cycles instead of resetting to 1
- **Auto-injection runs alongside explicit patterns** — `auto_inject_patterns()` now fires even when the user has explicit `applied_pattern_ids`, merging both sources. Previously, explicit selection completely disabled auto-injection
- **Cross-cluster injection threshold lowered** — `CROSS_CLUSTER_MIN_SOURCE_COUNT` reduced from 3 to 2, widening the pattern supply pipeline

### Fixed
- **MCP server 406 flood from health probes** — backend health endpoint's cross-service MCP probe now sends the required `Accept: application/json, text/event-stream` header. Previously, `httpx`'s default `Accept: */*` failed the Streamable HTTP transport's strict Accept validation, generating ~1 spurious 406 response per minute (139/day)
- **Warm path no-op cycling** — empty dirty set was coerced to `None` (interpreted as "scan all"), causing 28+ full 7-phase warm cycles per day with 0 operations. Now short-circuits immediately when no clusters are dirty. Also excluded `candidate_evaluation` trigger from re-firing the warm path timer (self-re-trigger from Phase 0.5)
- **Cross-cluster injection provenance** — cross-cluster pattern injections now create `OptimizationPattern` records with `source_id` and proper provenance tracking. Previously, only topic-based and global injections were tracked
- **Domain node unique constraint for multi-project** — changed `UNIQUE(label) WHERE state='domain'` to `UNIQUE(COALESCE(parent_id, ''), label) WHERE state='domain'`. The old constraint blocked creating same-named domains (e.g. "general") under different projects, causing hot-path assignment failures
- **MCP embedding index loading** — MCP server now loads the embedding index from disk cache at startup, enabling pattern injection for MCP-routed optimizations. Previously, the MCP process had an empty index, causing all auto-injection to return 0 results
- **Domain node coloring** — hot-path cluster and domain creation now assigns OKLab colors automatically. Previously only the cold path colored nodes, leaving 20+ clusters and new domain nodes without colors in the topology graph
- **Project node member_count reconciliation** — warm path Phase 0 now reconciles project node member_count as domain child count (structural semantics), preventing topology graph from rendering projects as giant blobs sized by optimization count

## v0.3.28 — 2026-04-11

### Added
- **SSE connection health monitoring** — real-time latency tracking (p50/p95/p99 from rolling 100-event window), three-state degradation detection (healthy/degraded/disconnected), and exponential backoff reconnection (1s-16s cap, 10-attempt limit, ±20% jitter). Compact StatusBar indicator with hover tooltip shows connection quality and retry status
- **SSE query param replay** — `GET /api/events?last_event_id=N` fallback for manual reconnection replay when browser `Last-Event-ID` header is unavailable
- **Repo index incremental refresh** — background periodic refresh cycle (configurable interval, default 600s) detects changed/added/deleted files via GitHub tree SHA comparison and updates the index incrementally instead of full reindex. Unique composite index on `(repo_full_name, branch, file_path)`
- **Per-project scheduler budgets** — replaced single-project round-robin with proportional per-project budget allocation in `AdaptiveScheduler`. Each linked project gets an independent quota (proportional to dirty cluster share, minimum floor of 3), per-project starvation counters with boost from largest donor, and observable metrics via `snapshot()`. All projects with dirty clusters served every warm cycle

### Fixed
- **Curated retrieval budget waste** — packing loop now uses skip-and-continue (bounded 5-skip window) instead of hard-break on oversized files. Budget utilization recovered from 50% to 98% on plan-dominated prompts
- **Source-type blindness in curated retrieval** — doc/plan files no longer crowd out implementation code. Source-type soft cap (`INDEX_CURATED_DOC_CAP_RATIO=0.35`) defers excess docs, letting code files fill priority slots
- **Import-graph inert for documentation** — markdown files with backtick file-path references now trigger doc-ref expansion, surfacing referenced code files the same way import-graph works for code

## v0.3.27 — 2026-04-11

### Added
- **Full source context delivery** — curated retrieval now delivers actual file source code to the optimizer instead of 500-char outlines. `RepoFileIndex.content` column stores full file content during indexing
- **Import-graph expansion** — after selecting top files by embedding similarity, the retrieval pipeline parses their import statements and pulls in dependency files (e.g., `models.py` from `repo_index_service.py`). Interleaved budget packing ensures dependencies get priority over low-scoring similarity tail files
- **Test file exclusion** — `_is_test_file()` removes test/spec/benchmark/fixture files from the index (39% reduction for typical codebases). Covers Python, TypeScript, Jest, Vitest, Playwright, Cypress patterns
- **Cross-domain noise filter** — files from a known domain different from the prompt's domain face a stricter 0.30 similarity floor (vs 0.20 base), eliminating frontend noise in backend prompts
- **Performance signals** — strategy performance by domain+task_type, anti-pattern hints (strategies averaging below 5.5), and domain vocabulary keywords injected into the optimizer at ~150 tokens cost
- **Context diagnostic panel** — collapsible CONTEXT section in the ForgeArtifact result view showing selected files with scores, import-graph expansions, budget utilization, stop reason, and near misses
- **Pipeline observability** — structured logging at 5 stages: curated retrieval (with cross-domain/diversity stats), import-graph expansion, retrieval detail (budget/timing), enrichment assembly (total context size), and optimizer injection (per-component char breakdown)
- **History project filter** — compact `<select>` dropdown in History panel header filters optimizations by linked project
- **History pagination** — "Load more" button appends next 50 items. Resets on SSE invalidation
- **Topology empty state** — Pattern Graph shows guidance message when taxonomy has no clusters
- **Cross-tab cluster scroll-to** — selecting a cluster from History or Topology scrolls the ClusterNavigator to the matching row
- **Synthesis status in Navigator** — Info tab shows color-coded synthesis status (cyan=ready, amber=pending/running, red=error)
- **`init.sh reload-mcp`** — restarts only the MCP server (faster than full restart, requires `/mcp` reconnect)

### Changed
- **Scoring formula v3** — rebalanced dimension weights: faithfulness 0.25→0.26, clarity/specificity 0.20→0.22, conciseness 0.20→0.15. Conciseness brevity-bias fixed ("SHORT IS NOT CONCISE" calibration), faithfulness originality-bias fixed, structure scores format-match not format-presence
- **Optimizer prompt tuning** — task-type depth scaling (specs/agentic=high detail, bug fixes=low), "maximize useful detail, not brevity" principle, dynamic format based on scope and risk surface
- **Brand compliance** — replaced 60+ hardcoded `rgba()` and hex values across 15 components with `color-mix()` design tokens. Removed 5 `backdrop-filter: blur()` instances. Normalized transition timing outliers
- **Curated retrieval cap raised** — `INDEX_CURATED_MAX_CHARS` 30K→80K, `INDEX_OUTLINE_MAX_CHARS` 500→2000, `INDEX_CURATED_MIN_SIMILARITY` 0.30→0.20
- **Heuristic analysis for all tiers** — runs for internal/sampling tiers (not just passthrough) to provide domain detection for cross-domain retrieval filtering
- **Explore file ranking uses pre-computed index embeddings** — `CodebaseExplorer._rank_files()` queries `RepoFileIndex` embeddings instead of creating ephemeral path-only embeddings
- **Feedback inline update** — `feedback_submitted` SSE updates history row in place instead of full re-fetch
- **Background synthesis deduplicated** — extracted `_run_explore_synthesis()` shared helper

### Fixed
- **Intent labels with parenthetical qualifiers** — Haiku appending "(Fully)", "(Complete)" etc. Fixed via analyze.md instruction + `_TRAILING_PAREN_RE` safety net in `validate_intent_label()`
- **Explore synthesis silently failing** — added `synthesis_status`/`synthesis_error` columns to track lifecycle
- **CLI provider argument overflow** — user_message piped via stdin instead of CLI arg (prevents `ARG_MAX` on large repos)
- **Inspector shows project names** instead of count for multi-project clusters
- **connectionState returning 'ready' while indexing** — added pending/indexing to in-progress status list
- **Project nodes missing from cluster tree** — `get_tree()` state filter now includes `"project"` state

## v0.3.25 — 2026-04-10

### Fixed
- **Auto-update stable-only detection** — restored pre-release/dev tag filtering in `_parse_latest_tag()`. Only stable releases created via `./scripts/release.sh` trigger auto-update notifications. Clarified docstrings

## v0.3.24 — 2026-04-10

### Added
- **Unified `GitHubConnectionState` model** — 5-state getter (`disconnected`/`expired`/`authenticated`/`linked`/`ready`) replaces scattered null checks across all components. Single source of truth for GitHub connection status
- **GitHub avatar in StatusBar** — 16px profile picture mini-badge between tier indicator and connection status. Username tooltip on hover
- **Connection status indicators** — StatusBar shows state-specific text (repo name / `indexing...` / `expired` / `no repo`) with semantic colors. GitHub panel header shows matching badge
- **Auth-expired reconnect banner** — appears inside the linked-repo Info tab when token expires, with one-click `reconnect()` that clears stale state and starts Device Flow
- **GitHub OAuth token refresh** — stored `refresh_token` + `expires_at` from Device Flow. `_get_session_token()` auto-refreshes expired access tokens. `github_me` validates live with GitHub API
- **Project visibility across UI** — Inspector shows project breadcrumb on clusters (single + multi-project), repo context row in optimization detail. ForgeArtifact shows `repo_full_name` below header. History rows show 2-letter project abbreviation badges
- **Legacy project node** — pre-link optimizations reassigned to "Legacy" project (171 records), distinguishing them from post-link optimizations in history badges
- **Repo picker enhancements** — shows description (truncated 60 chars), star count, private badge, last updated timestamp per repository
- **GitHub Info tab improvements** — shows `linked_at` timestamp, project short name (full path in tooltip), connection status badge
- **GitHub connection state design spec** — `docs/superpowers/specs/2026-04-10-github-connection-state-design.md`
- **GitHub connection state implementation plan** — `docs/superpowers/plans/2026-04-10-github-connection-state.md`

### Fixed
- **Cross-component reactivity (12 fixes)** — F1: centralized MCP SSE handling via `forgeStore.handleExternalEvent()`. F3: async `invalidateClusters()` prevents ghost cluster selection. F4: refinement init generation guard. F5: `reloadTurns` public for cross-tab SSE. F6: per-tab feedback caching. F8: persistent seed batch progress survives modal close. F9: preference toggle rollback on API failure. F10: topology click dispatches `switch-activity`. F11: Inspector shows selected refinement version ScoreCard. F13: auto-switch to editor on forge complete. F15: project badge in StatusBar. F16: GitHub unlink clears cluster selection
- **GitHub reconnect button was dead code** — `_handleAuthError()` set `user=null` alongside `authExpired=true`, making the button's `{:else if githubStore.user}` branch permanently unreachable. New `reconnect()` method clears `linkedRepo` first so template falls to Device Flow branch
- **`authExpired` flag stuck after logout** — `checkAuth()` null path and `logout()` now reset `authExpired`. `checkAuth()` null path also clears stale `linkedRepo`
- **GitHub token 8-hour expiry** — GitHub App has "Expire user authorization tokens" enabled but code only stored `access_token`, discarding `refresh_token`. Tokens now stored with expiry metadata and auto-refresh
- **`repo_full_name` not persisted on passthrough tier** — both inline and standalone passthrough `Optimization` constructors were missing the field
- **`repo_full_name` not passed from REST optimize router** — `orchestrator.run()` call now includes `repo_full_name=effective_repo`
- **`LinkedRepo.id` type mismatch** — frontend required `id: string` but backend never returned it. Removed from interface
- **GitHub panel brand compliance** — fixed 16 undefined CSS variables (`--color-border`, `--color-text`, `--color-surface-hover`). Unified tab styling with ClusterNavigator pattern (24px height, 600 weight, uppercase, color-mix hover). Compacted search input, file tree items, repo items to brand density spec. Fixed padding violations (max 6px sidebar rule). Added ARIA tab attributes. Replaced hardcoded rgba with `color-mix()` tokens

### Changed
- **StatusBar GitHub indicator** — replaced simple project badge with connection-state-aware display showing all 5 states
- **Navigator GitHub tabs** — unified with ClusterNavigator `.state-tab` pattern (24px height, uppercase, font-weight 600, spring transitions, color-mix hover)
- **`github_me` endpoint validates live** — calls GitHub API instead of returning cached DB data. Cleans up stale token + linked repo on revocation

## v0.3.23 — 2026-04-10

### Added
- **`scripts/release.sh`** — one-command release workflow: version sync, changelog extraction, commit, tag, push, GitHub Release creation (with changelog body), dev bump. Requires `gh` CLI
- **UpdateBadge indicator dot** — pulsing green dot in top-right corner for better discoverability

### Fixed
- **UpdateBadge dialog not opening** — `overflow: hidden` on StatusBar clipped the popup. Dialog now uses `position: fixed` with coordinates from `getBoundingClientRect()`. Click-outside handler deferred via `setTimeout(0)` to prevent open-then-close race
- **UpdateBadge brand compliance** — explicit `border-radius: 0` on badge, NEW tag, buttons. Custom checkbox replaces browser default with industrial aesthetic
- **init.sh `_do_update` path resolution** — `_REAL_SCRIPT_DIR` now fails explicitly if unset (was silent fallback to `/tmp/`). All paths use `$BACKEND_DIR`/`$FRONTEND_DIR`
- **init.sh alembic failure handling** — migration errors now roll back `git checkout` and exit (was warn-and-continue)
- **init.sh post-checkout validation** — venv sanity check + alembic `(head)` check added to validation output
- **Update 202 response race** — deferred restart spawn via `asyncio.sleep(1)` so HTTP response flushes before backend kill

## v0.3.20 — 2026-04-10

### Added
- **Auto-update system** — 3-tier version detection (git tags, raw GitHub fetch, Releases API). Persistent StatusBar badge, one-click update dialog with changelog + detached HEAD warning. Two-phase trigger-and-resume architecture. Post-update validation suite (version, tag, migration checks). CLI: `./init.sh update [tag]`
- **`GET /api/update/status`** — cached update check result (version, tag, changelog, detection tier)
- **`POST /api/update/apply`** — trigger update + detached restart (202 Accepted)

### Fixed
- **RepoIndexMeta duplicate rows** — unique constraint on `(repo_full_name, branch)` + Alembic migration to deduplicate. Race condition in `_get_or_create_meta()` replaced with SQLite `INSERT...ON CONFLICT DO NOTHING`
- **PipelineResult ValidationError** — `context_sources` field widened to accept mixed-type dicts (booleans + nested enrichment metadata). Added `@field_validator` coercion + try/except fallback to prevent lost LLM work
- **Cold-path `_last_silhouette` leak** — save/restore on quality gate rejection prevents Q system corruption after rejected refits
- **162 orphaned `project_id` references** — data migration + project_id reconciliation added to `repair_data_integrity()`
- **No `taxonomy_changed` SSE after recluster rollback** — frontend now notified even on cold-path rejection
- **Pattern extraction crash** — guard against optimizations missing `optimized_prompt`
- **Enrichment trace `repo_full_name` null** — falls back to `enrichment_meta` nested value
- **GitHub 401 silent failure** — added `logger.warning` for token expiry visibility
- **Flaky integration test** — hardened `next()` calls with safe default + diagnostic assertion
- **Subprocess timeout consistency** — all subprocess calls in UpdateService have explicit `asyncio.wait_for()` timeouts

## v0.3.20-dev — 2026-04-09

### Added
- **VS Code frictionless setup** — `./init.sh setup-vscode` detects VS Code across standard, snap, flatpak, Insiders, Codium, and custom paths, then installs/updates the MCP Copilot Bridge extension. Auto-installs on `./init.sh start` (silent when up-to-date)
- **Provider detection in init.sh** — detects Claude CLI (OAuth/MAX), `ANTHROPIC_API_KEY` (env), and stored API credentials. Shows active routing tier preview (internal/sampling/passthrough) on start/restart
- **VS Code bridge health probe** — post-start JSON-RPC initialize request validates MCP sampling endpoint. Targeted diagnostics on failure (not running, timeout, HTTP error)
- **Pipeline status dashboard** — `./init.sh status` shows provider, VS Code, bridge version, MCP health, sampling config, native discovery, and active tier in a single view
- **Landing page "Launch App" links** — primary CTA in hero, navbar, and trust section. App URL printed on every `./init.sh start`
- **Dynamic changelog** — `/changelog` page auto-renders from `docs/CHANGELOG.md` via Vite `?raw` import. No manual frontend updates needed
- **Landing page beta update** — all sections updated for v0.3.19 capabilities: 13 tools, 6 strategies, 3-tier routing, evolutionary knowledge graph, new Capabilities section (6 cards: refinement, seeding, codebase context, observability, learning loops, multi-project)
- **Refinement trade-off awareness** — `suggest.md` receives score deltas and trajectory (improving/degrading/oscillating). Net-positive impact required, conciseness guard when <6.0, anti-circular suggestions, dimension protection >7.5
- **Refinement score guardrails** — `refine.md` receives current scores and strongest dimensions. Compression directive prevents length bloat. Trade-off rule prevents net-negative changes
- **Brand guidelines in repo** — `.claude/skills/brand-guidelines/` surfaced for contributors (SKILL.md + 3 reference files)

### Changed
- **init.sh startup flow** — bridge install moved to pre-start (Phase 1), services launch (Phase 2), health verification (Phase 3). Bridge ready before MCP server comes up
- **Suggestion chips vertical layout** — full-text display (was 200px truncated). Tooltip shows suggestion text (was showing source field). Column layout replaces inline row
- **Conciseness heuristic rebalanced** — tiered structural density bonus: +1.0 base, +0.5 per structural tier (cap +3.0). Code + headers = info-dense format. Prevents structured prompts from being penalized for domain-term repetition
- **Integrations section reframed as routing tiers** — Passthrough / IDE Sampling / Internal Provider (was Zero Cost / Your IDE / Codebase-Aware)
- **Inspector model display** — shows model for current phase (was showing last-received model from previous phase)

### Fixed
- **Health probe false sampling registration** — `init.sh` health check sent `capabilities: { sampling: {} }`, causing sampling_capable flap on every startup. Now uses empty capabilities
- **Refinement stream resilience** — added `serverConfirmed` flag, generation-based cancellation, 20s recovery polling. Handles hot-reload, network drops, rapid cancel
- **Refinement race condition** — recovery polling loop now cancelled by generation counter when new refine/cancel/reset starts
- **Session restore suggestions** — `loadFromRecord` now populates `initialSuggestions` from DB. Suggestions survive page reload and session restore
- **Pipeline running events missing model** — all 4 phase running events (analyze, optimize, score, suggest) now include the resolved model ID
- **Navbar button alignment** — consistent 22px height, flexbox gap, unified CTA styling
- **Missing @keyframes phase-type-in** — mockup animation was silently broken
- **Section numbering** — HTML + CSS comments renumbered after Capabilities section insert
- **Footer label mismatch** — "Live Example" → "Example" to match navbar
- **Focus-visible states** — added on all interactive elements (navbar, buttons, footer, cards)
- **Unused Logo import** — removed from landing page script block
- **Changelog parser type safety** — validates category labels before unsafe cast
- **MCP tool count** — updated from 11/12 to 13 across CLAUDE.md, README.md, AGENTS.md, ADR-001, and VS Code bridge package.json
- **Bridge extension metadata** — added `synthesis_seed` and `synthesis_explain` to `languageModelTools` and `languageModelToolSets` (was 11, now 13)
- **Batch pipeline suggest template** — added missing `score_deltas`/`score_trajectory` variables

## v0.3.19-dev — 2026-04-09

### Added
- **GitHub Device Flow OAuth** — zero-config authentication using hardcoded GitHub App client ID. No client secret or callback URL required. Gated handoff UX: shows device code first, user clicks to open GitHub
- **GitHub repo picker** — search repos, select existing project or auto-create on link. `project_id` parameter on link endpoint for explicit project selection
- **GitHub file browser** — recursive file tree, single file content viewer, branch listing. 5 new endpoints: `tree`, `files/{path}`, `branches`, `index-status`, `reindex`
- **Background repo indexing** — `RepoIndexService.build_index()` + `CodebaseExplorer.explore()` triggered as background task on repo link and reindex. Haiku architectural synthesis cached in `RepoIndexMeta.explore_synthesis`
- **Unified codebase context pipeline** — two-layer architecture: cached explore synthesis (architectural overview) + per-prompt curated retrieval (semantic file search, 30K char cap). All tiers use identical pre-computed context via `ContextEnrichmentService.enrich()`. 5-min TTL cache on curated queries
- **ADR-006: Universal Prompt Engine** — formal architectural decision documenting domain-agnostic design. Extension points are content additions (seed agents, domain keywords, context providers), not code changes
- **74 missing taxonomy tests** — Phase 2B (16 tests: validation lifecycle, retention cap) and Phase 3B (58 tests: HNSW backend, auto-selection, cache, snapshot, benchmark). Total: 1872 backend tests
- **Frontend-backend wiring** — 7 type gaps fixed: `project_node_id`/`project_label` on LinkedRepo, `project_id` on HistoryItem, `project_ids`/`member_counts_by_project` on ClusterDetail, `project_count`/`global_patterns` on HealthResponse

### Changed
- **`INDEX_CURATED_MAX_CHARS` raised from 8000 to 30000** — ~8K tokens of file outlines per optimization instead of ~2K
- **Codebase context resolved once per request** — all tiers use unified enrichment instead of per-tier explore calls. Zero request-time LLM calls for codebase context
- **Branch resolution from LinkedRepo** — context enrichment, pipeline, and sampling pipeline resolve branch from DB instead of defaulting to "main"
- **Legacy project permanence** — `ensure_project_for_repo()` never renames Legacy. Always creates new project for linked repos, preserving pre-repo optimization history
- **Reindex triggers explore synthesis** — was previously only triggered on initial repo link

### Removed
- **`SamplingLLMAdapter`** — dead code. Wrapped CodebaseExplorer for per-request Haiku calls; replaced by pre-computed background synthesis
- **`_run_explore_phase()`** — dead code in sampling pipeline. Phase 0 now uses pre-computed context from enrichment service
- **Phase 0 explore in internal pipeline** — was dead code (no caller passed `github_token`). Now handled by ContextEnrichmentService

### Fixed
- **Gated device flow handoff** — auto-opened GitHub tab before showing device code. Now shows code first, user clicks button to proceed
- **StatusBar breadcrumb truncation** — removed 300px max-width that cut off intent labels
- **Linked repo project_label** — re-fetches via `loadLinked()` after link to show project label immediately
- **CI test failures** — health endpoint probes fail in CI (added `?probes=false`), spectral split environment-sensitive (accept both None and low-silhouette)

## v0.3.18-dev — 2026-04-08

### Added
- **ADR-005: Taxonomy scaling architecture (Phases 1-3B)** — complete multi-project isolation and performance scaling. 1796 tests, 60 spec requirements verified. Key capabilities:
  - **Multi-project isolation**: project nodes created on GitHub repo link, two-tier cluster assignment (in-project first, cross-project fallback +0.15 boost), per-project Q metrics in warm path speculative phases
  - **Global pattern tier**: durable cross-project patterns promoted from MetaPattern siblings (2+ projects, 5+ clusters, avg_score >= 6.0), injected with 1.3x relevance boost, validated with demotion/re-promotion hysteresis (5.0/6.0), 500 retention cap with LRU eviction
  - **Round-robin warm scheduling**: linear regression boundary computation, all-dirty vs round-robin mode decision, starvation guard (3-cycle limit), per-project dirty tracking
  - **HNSW embedding index**: dual-backend (`_NumpyBackend` + `_HnswBackend`), stable label mapping with tombstones, auto-selects HNSW at >= 1000 clusters on rebuild
- **`EXCLUDED_STRUCTURAL_STATES` constant** — centralized frozenset replacing 37+ inline `["domain","archived"]` patterns across 13 files. Adding a new structural state is a one-line change
- **`GlobalPattern` model** — 11-column table for cross-project patterns that survive cluster archival. Promoted from MetaPattern, injected alongside cluster patterns
- **`project_id` on Optimization** — denormalized FK for fast per-project filtering, backfilled from cluster ancestry
- **Legacy project node migration** — idempotent startup migration creates Legacy project, re-parents domain nodes, backfills project_id
- **`project_service.py`** — `ensure_project_for_repo()` (Legacy rename, new project, re-link) + `resolve_project_id()` for session-based project resolution
- **`global_patterns.py`** — promotion pipeline (sibling discovery, dedup), validation lifecycle (demotion/re-promotion/retirement), retention cap enforcement. Phase 4.5 in warm path
- **Topology project filter** — `GET /api/clusters/tree?project_id=...` for project-scoped subtrees, `member_counts_by_project` on cluster detail, `project_count` on health endpoint
- **`global_patterns` health stats** — `GET /api/health` returns active/demoted/retired/total counts
- **Cross-service health probes** — `GET /api/health` probes all three services with 5s timeout
- **Monitoring data export** — `GET /api/monitoring` with uptimes and LLM latency percentiles
- **Structured error logging** — `ErrorLogger` with 30-day JSONL rotation
- **Split failure events** — `split/insufficient_members` and `split/too_few_children` decision events
- **Sparkline oscillation fix** — cold path rejection snapshots carry forward `q_health`
- **Sampling regression test suite** — 20 pytest cases covering 7 known bugs
- **init.sh graceful retry** — 3 retries with exponential backoff on service startup
- **Cross-domain outlier reconciliation** — Phase 0 ejects cross-domain members

### Changed
- **Logging level consistency normalized** — merge-back, pattern extraction, batch pipeline failures promoted to warning
- **Analyzer "saas" domain classification tightened** — explicit decision criteria

### Fixed
- **50+ silent failure paths instrumented** — systematic observability audit across all taxonomy paths
- **Pipeline embedding failure now logged** — was silent `except Exception: pass`
- **Pattern injection silent drops visible** — `np.frombuffer` failures now warned
- **Sampling pipeline structured fallback monitored** — `optimization_status` event emitted
- **JSONL readers warn on malformed lines** — `trace_logger.py` and `event_logger.py`
- **Event logger singleton warnings rate-limited** — max 5 warnings before init
- **Dissolution cascade cross-domain contamination** — `_reassign_to_active()` domain-aware
- **Lifecycle dead zone for mid-size clusters** — `SPLIT_MIN_MEMBERS` 25->12, `FORCED_SPLIT_COHERENCE_FLOOR` 0.25->0.35

## v0.3.17-dev — 2026-04-07

### Added
- **Cancel button during pipeline** — SYNTHESIZE button becomes CANCEL (neon-yellow accent) during analyzing/optimizing/scoring phases
- **Elapsed timer in StatusBar** — shows seconds elapsed next to phase progress during active pipeline execution
- **Seed pipeline service integration** — batch seeding now has near-parity with the regular internal pipeline: pattern injection, few-shot example retrieval, adaptation state, domain resolution, historical z-score normalization, and heuristic flag capture. Seeds get the same context enrichment as interactive optimizations
- **Seed quality gate** — `bulk_persist()` filters seeds with `overall_score < 5.0` before persisting, preventing low-quality seeds from polluting the taxonomy and few-shot pool
- **Suggestion generation for seed prompts** — batch pipeline now runs Phase 3.5 (suggest.md) when scoring completes, producing 3 actionable suggestions per seed. Previously seeds had `suggestions=null`, breaking the refinement UX
- **Refinement context enrichment** — the `/api/refine` endpoint now passes workspace guidance and adaptation state to `create_refinement_turn()`. Previously all enrichment kwargs were `None`, producing weaker refinement for all prompts
- **Intent density optimization for agentic executors** — optimizer taught 4 techniques: diagnostic reasoning, decision frameworks, vocabulary precision, outcome framing. Targets AI agents with codebase access (Claude Code, Copilot) where intent sharpening matters more than structural enhancement
- **Forced split for large incoherent clusters** — clusters with 6-24 members and coherence < 0.25 now eligible for spectral split, closing the gap between dissolution (≤5 members) and normal split (≥25 members)
- **Scoring calibration for expert diagnostic prompts** — added clarity/specificity/conciseness calibration examples for investigation prompts using vocabulary precision rather than format structure. Scorer no longer under-rates expert-level concise prose

### Changed
- **Intelligence layer principle in optimizer** — rewrote codebase context guidance in `optimize.md` from passive caveat to first-class principle with good/bad examples and "Respect executor expertise" guideline
- **Scoring dimension weights rebalanced** — conciseness raised from 0.10 to 0.20. New weights: clarity 0.20, specificity 0.20, structure 0.15, faithfulness 0.25, conciseness 0.20. All pipelines import `DIMENSION_WEIGHTS` from `pipeline_contracts.py`
- **Heuristic scorer recalibrated** — faithfulness similarity-to-score mapping fixed (sim 0.5 now maps to 7.0, was 5.0). Specificity base raised 2.5→3.0 with density normalization. Fixed `_RE_XML_OPEN` trailing comma (dormant tuple bug)
- **Strategy `auto` resolves to named strategy** — `resolve_effective_strategy()` now maps `auto` to task-type-appropriate named strategies (coding→meta-prompting, writing→role-playing, data→structured-output). Optimizer always gets concrete technique guidance instead of generic "do whatever"
- **Chain-of-thought strategy updated** — debugging/investigation prompts moved from "When to Use" to "When to Avoid" to prevent prescriptive step enumeration for expert executors
- **Taxonomy quality gates tightened** — cold-path `COLD_PATH_EPSILON` reduced 0.08→0.05 (rejects >5% Q drops). Warm-path epsilon base 0.01→0.006 (rejects >0.5% merge regressions)
- **HDBSCAN noise reduction** — added `min_samples=max(1, min_cluster_size-1)` to reduce cold-path noise rate. Added `hasattr` guard for `condensed_tree_` attribute compatibility
- **Pattern extraction lowered to 1 member** — warm-path Phase 4 `refresh_min_members` reduced from 3 to 1. Even singleton clusters now get meta-patterns extracted, fixing 74% of clusters showing "No meta-patterns extracted yet"
- **OptimizationPattern repair improved** — warm-path Phase 0 now migrates stale OP records to the optimization's current cluster instead of deleting them, and backfills missing source records. Prevents prompts from vanishing after cluster merges
- **Scoring rubric anti-patterns** — added prescriptive-methodology anti-pattern to structure dimension and faithfulness calibration for methodology scope-creep

### Fixed
- **Seed prompts unclickable in history** — `OptimizationDetail.context_sources` was typed `dict[str, bool]` but seeds store string metadata. Pydantic validation error → 500 on `GET /api/optimize/{trace_id}`. Widened to `dict | None`. Added error toast in Navigator catch block
- **Atomic OptimizationPattern updates in cluster mutations** — `attempt_merge()` and `attempt_retire()` updated `Optimization.cluster_id` but not `OptimizationPattern.cluster_id`, causing join records to point to archived clusters. Prompts vanished from cluster detail views. Fixed: OP records now migrated atomically in merge, retire, and hot-path reassignment
- **Cross-process event forwarding** — 5 failure points in MCP→backend→SSE chain fixed (sync fallback, lazy init, bounded retry queue, replay buffer sizing, dedup suppression)
- **Leaked MetaPatterns from archived clusters** — 85% of meta-patterns belonged to archived clusters, inflating `global_source_count` and injecting dead patterns. Fixed cleanup on split + archived-state filter
- **Snapshot table unbounded growth** — wired `prune_snapshots()` into warm-path after Phase 6 audit
- **Score-weight formula mismatch** — unified power-law centroid weighting across hot/warm/cold paths
- **Merge centroid weighted by count** — fixed to use `weighted_member_sum` for centroid blending
- **Sampling proxy hang** — removed broken MCP sampling proxy, requests degrade cleanly

### Removed
- **Dead sampling proxy code** — removed broken proxy and recovery setTimeout branches in forge store

## v0.3.16-dev — 2026-04-05

### Added
- **Diegetic UI for Pattern Graph** — Dead Space-inspired immersive interface replacing all persistent overlays. Default view shows only ambient telemetry (`46 clusters · MID` at 40% opacity). Controls auto-hide on right-edge hover (50px zone, 2s fade delay). Metrics panel toggled via Q key. Search via Ctrl+F. All overlays dismissable via click/Escape
- **Inline hint card** — compact shortcut cheat-sheet (7 shortcuts + 3 visual encoding hints) replaces the TierGuide modal wizard. Shows once on first visit, `?` button re-opens. Tier-aware accent color. Dismissable via click/Escape/backdrop
- **Cluster dissolution** — small incoherent clusters (coherence < 0.30, ≤5 members, ≥2h old) dissolved and members reassigned to nearest active cluster. Runs in Phase 3 (retire), Q-gated. `retire/dissolved` event with full context
- **State filter graph dimming** — switching navigator tabs dims non-matching nodes to 25% opacity in the 3D graph. Matching nodes at 100%, domains at 50%. Labels suppressed for dimmed nodes
- **Auto-switch navigator tab** — clicking a cluster (from Activity panel, graph, or search) auto-switches the sidebar tab to match the cluster's state. Skips auto-switch for orphan clusters
- **Activity panel cluster navigation** — clicking cluster IDs in the Activity feed selects the cluster, pans the 3D camera, loads the Inspector, and auto-switches the navigator tab

### Changed
- **State filter tabs redesigned** — clean bottom-border accent in state's own color (chromatic encoding), monospace font, 3-char labels (ALL/ACT/CAN/MAT/TPL/ARC), `flex:1` equal width
- **Activity panel redesigned** — mission control terminal aesthetic. Path chips with 6px colored dots (uppercase), op chips dimmed at 55% opacity. Severity-driven event rows: 2px left accent rail by path color, error rows with red tint, info rows dimmed to 50%. Cluster links hidden by default (visible on hover). Expanded context slides in with animation
- **Phase 4 pattern extraction parallelized** — pre-computes taxonomy context sequentially, runs all LLM calls in parallel via `asyncio.gather`. ~25x speedup (800s → ~30s)
- **Sub-domain evaluation noise eliminated** — only logs when `would_trigger=True` (961→0 events/day)
- **InfoPanel grid borders softened** — transparent background, 40% opacity separators instead of solid grid lines
- **Archived state color brightened** — `#2a2a3e` → `#3a3a52` for better contrast on dark backgrounds

### Fixed
- **Right-edge hover detection** — `.hud` had `pointer-events:none` blocking all mouse events. Fixed with dedicated edge-zone div with `pointer-events:auto`
- **Cluster ID click in Activity panel** — was dispatching unhandled CustomEvent. Now calls `clustersStore.selectCluster()` directly
- **Session restore 404** — startup loaded optimization with deleted `cluster_id`. Guard checks tree before calling `selectCluster`
- **Cluster load failure retry loop** — 404 on deleted cluster left `selectedClusterId` set, causing infinite retry. Now clears selection on failure
- **Topology showed filtered nodes** — graph used `filteredTaxonomyTree` (changed with tabs). Fixed to use full `taxonomyTree`; `buildSceneData` filters archived
- **setStateFilter always cleared selection** — now preserves selection if cluster would remain visible in new filter
- **Navigator page size** — bumped 50→500 to eliminate hidden clusters below fold
- **errorsOnly filter** — now catches `seed_failed` and `candidate_rejected` events
- **Decision badge text overflow** — long names like `sub_domain_evaluation` truncated with `flex-shrink:1` + `max-width`
- **DB session safety in Phase 4** — parallel pattern extraction shared DB session across coroutines. Pre-computes taxonomy context sequentially, parallel phase is LLM-only
- **Candidate reassignment cascade** — rejected members could be assigned to sibling candidates. Now excludes all candidate IDs from reassignment targets
- **3 svelte-check warnings resolved** — SeedModal tabindex, label→span, unused CSS

## v0.3.15-dev — 2026-04-04

### Added
- **Spectral clustering for taxonomy splits** — replaced HDBSCAN as primary split algorithm. Spectral finds sub-communities via similarity graph structure, solving the uniform-density problem where HDBSCAN returned 0 clusters. Tries k=2,3,4 with silhouette gating (rescaled [0,1], gate=0.15). HDBSCAN retained as secondary fallback. K-Means fallback removed (spectral subsumes it)
- **Candidate lifecycle for split children** — split children start as `state="candidate"` instead of active. Warm-path Phase 0.5 (`phase_evaluate_candidates()`) evaluates each candidate: coherence ≥ 0.30 → promote to active, below floor → reject and reassign members to nearest active cluster via `_reassign_to_active()`. Candidates excluded from Q_system computation in speculative phases to prevent low-coherence candidates from causing Q-gate rejection of the split that created them
- **Candidate visibility in frontend** — candidate filter tab in ClusterNavigator with count badge when candidates > 0. Candidate nodes render at 40% opacity in topology graph with label suppression. Inspector shows CANDIDATE badge. "Promote to Template" button hidden for candidates
- **5 new observability events** — `candidate_created` (cyan), `candidate_promoted` (green), `candidate_rejected` (amber), `split_fully_reversed` (amber), `spectral_evaluation` (split trace with per-k silhouettes). All events include full context for audit: coherence, coherence_floor, time_as_candidate_ms, members_reassigned_to, parent_label
- **Activity panel candidate support** — `candidate` op filter chip, `keyMetric` handlers for all candidate events + `spectral_evaluation`, `decisionColor` entries. Toast notifications for promotion, rejection, and split-with-candidates
- **Cold-path cluster detail event** — `refit/cluster_detail` logs every cluster ≥5 members after recluster with label, member_count, domain, coherence
- **Activity panel JSONL merge on startup** — ring buffer + today's JSONL merged when buffer has <20 events, preventing the "2 events after restart" problem
- **`assign/merge_into` events enriched** — now include `member_count` and `prompt_label` for Activity panel display
- **`seed_prompt_failed` color changed from red to amber** — individual prompt failures are expected (fail-forward), not catastrophic

### Changed
- **Sub-domain evaluation noise reduced** — only logs when domain is ≥75% of member threshold (760/day → ~20/day)

### Fixed
- **Activity panel showed only 2 events after restart** — JSONL fallback only triggered when ring buffer was completely empty (0 events). Two warm-path events prevented fallback, leaving users with zero historical context
- **Event context key mismatches** — `candidate_promoted`/`rejected` used `label` instead of spec's `cluster_label`, missing `coherence_floor`, `members_reassigned_to`, `reason` fields. All context keys now match spec exactly
- **`candidate_created` event field names** — `members` → `child_member_count`, `coherence` → `child_coherence` per spec

## v0.3.14-dev — 2026-04-04

### Added
- **Batch seeding system** — explore-driven pipeline that generates diverse prompts from a project description, optimizes them through the full pipeline in parallel, and lets taxonomy discover structure organically. Four-phase architecture: agent generation → in-memory batch optimize → bulk persist → batched taxonomy integration
- **Seed agent definition system** — 5 default agents in `prompts/seed-agents/*.md` (coding, architecture, analysis, testing, documentation) with YAML frontmatter, hot-reload via file watcher, user-extensible by dropping `.md` files
- **`AgentLoader` service** — file parser for seed agent frontmatter (name, description, task_types, phase_context, prompts_per_run, enabled). Mirrors `StrategyLoader` pattern
- **`SeedOrchestrator` service** — parallel agent dispatch via `asyncio.gather`, embedding-based deduplication (cosine > 0.90), scales `prompts_per_run` to hit target count
- **`batch_pipeline.py`** — in-memory batch execution with zero DB writes during LLM-heavy portion. `PendingOptimization` dataclass, `run_single_prompt()` (direct provider calls, no `PipelineOrchestrator`), `run_batch()` (semaphore-bounded parallelism with 429 backoff), `bulk_persist()` (single-transaction INSERT with retry + idempotency), `batch_taxonomy_assign()` (cluster assignment with `pattern_stale=True` deferral), `estimate_batch_cost()` (tier-aware pricing)
- **`synthesis_seed` MCP tool** — 12th tool in MCP server. Accepts `project_description`, `workspace_path`, `prompt_count`, `agents`, or user-provided `prompts`. Returns `SeedOutput` with batch_id, counts, domains, clusters, cost estimate
- **`POST /api/seed`** — REST endpoint mirroring MCP tool for UI consumption. Resolves routing from `request.app.state.routing` (not MCP-only `_shared.py` singleton)
- **`GET /api/seed/agents`** — lists enabled seed agents with metadata for frontend agent selector
- **`SeedRequest`/`SeedOutput` schemas** — Pydantic models with `min_length=20` on project_description, `ge=5, le=100` on prompt_count. No `actual_cost_usd` field (estimation-only design)
- **`SeedModal.svelte`** — brand-compliant modal with Generate/Provide tabs, agent checkboxes, prompt count slider (5-100), cost estimate, progress bar via SSE, result card with copyable batch_id, status badge, stats grid, domain tags, tier badge, duration
- **Seed button in topology controls** — "Seed" button in `TopologyControls.svelte` opens `SeedModal` in `SemanticTopology.svelte`
- **`seed.ts` API client** — TypeScript interfaces (`SeedRequest`, `SeedOutput`, `SeedAgent`) and fetch functions (`seedTaxonomy`, `listSeedAgents`)
- **`seed_batch_progress` SSE handler** — `+page.svelte` receives SSE events, dispatches `seed-batch-progress` DOM CustomEvent for SeedModal progress bar
- **9 seed observability events** — `seed_started`, `seed_explore_complete`, `seed_agents_complete`, `seed_prompt_scored`, `seed_prompt_failed`, `seed_persist_complete`, `seed_taxonomy_complete`, `seed_completed`, `seed_failed` — all with structured context for MLOps monitoring (throughput, cost/prompt, failure rate, domain distribution)
- **ActivityPanel seed event rendering** — `keyMetric` handlers for all seed events showing scores, prompt counts, cluster counts, domain counts, error messages. Color mapping: `seed_failed` → red, `seed_prompt_failed` → amber, `seed_completed` → green, informational events → secondary

### Changed
- **Split test threshold updated** — `test_split_triggers_on_stale_coherence_cluster` updated from 14 → 26 members to match `SPLIT_MIN_MEMBERS=25` raised in v0.3.13-dev
- **Provider-aware concurrency** — batch seeding uses CLI=10, API=5 parallel for internal tier (distinguishes `claude_cli` from `anthropic_api` provider)

### Fixed
- **`routing.state.tier` crash** — `handle_seed()` accessed non-existent `RoutingState.tier` attribute; fixed to use `routing.resolve(RoutingContext)` returning `RoutingDecision.tier`, matching all other tool handlers
- **`PromptLoader._prompts_dir` AttributeError** — batch pipeline accessed private `_prompts_dir` attribute; corrected to public `prompts_dir`
- **`cluster_id` not written back in `batch_taxonomy_assign`** — taxonomy assignment created clusters but didn't update `Optimization.cluster_id` rows; added writeback matching engine.py hot-path pattern
- **Semaphore leak on 429 backoff** — rate-limit retry in `run_batch()` acquired extra semaphore slot without `try/finally`; fixed to ensure release on cancellation
- **SeedModal stale state on reopen** — closing and reopening modal showed previous result/error/progress; now resets transient state on open
- **Frontend validation mismatch** — SeedModal accepted 1-char descriptions but backend requires `min_length=20`; aligned to `>= 20`
- **Frontend cost estimate formula** — was `promptCount × agents × $0.002` (wrong); now mirrors backend `agents × $0.003 + prompts × $0.132`

## v0.3.13-dev — 2026-04-03

### Added
- **Sub-domain trigger evaluation logging** — `discover/sub_domain_evaluation` events emitted for each oversized domain showing member count, mean coherence, and whether the HDBSCAN threshold was met
- **Score event cross-process notification** — MCP process score events now reach the backend via HTTP POST (`/api/events/_publish`), bridging the inter-process gap so they populate the SSE stream
- **Score events populate backend ring buffer** — cross-process score events mirror into the in-memory ring buffer so `/api/clusters/activity` returns them after an MCP session
- **`intent_label` on score events** — Activity panel displays human-readable labels (e.g. `python-debugging-assistance`) instead of raw UUIDs on `score` operation events
- **Click score event `↗` button → load optimization in editor** — clicking the navigate icon on an Activity panel score event loads the optimization into the prompt editor
- **Domain node member_count reconciliation in warm-path Phase 0** — domain nodes are now included in the Phase 0 member_count reconciliation pass, fixing stale counts on domain nodes
- **Stale archived cluster pruning in Phase 0** — archived clusters older than 24 hours with zero members and no referencing optimizations are deleted in Phase 0 to prevent unbounded accumulation

### Changed
- **SSE keepalive timeout increased from 25s to 45s** — prevents EventSource disconnects during long-running warm-path operations that previously triggered client reconnects
- **SQLite busy timeout increased to 30s** — applies uniformly across backend PRAGMA, MCP PRAGMA, and SQLAlchemy `connect_args` to reduce lock contention errors
- **`improvement_score` wired into adaptation learning** — `fusion.py` now prefers `improvement_score` over `overall_score` for z-score weighting in `compute_score_correlated_target()`, improving signal quality for weight adaptation
- **Warm-path 30s debounce after `taxonomy_changed` events** — batches rapid SSE invalidation events to reduce SQLite write contention during active clustering
- **`SPLIT_MERGE_PROTECTION_MINUTES` constant** — value was hardcoded as 30 in earlier code; now a named constant set to 60 minutes (introduced in v0.3.12-dev, documented here as the canonical definition point)

### Fixed
- **Groundhog Day split loop (variant 3)** — same-domain merge during warm path reformed mega-clusters immediately after split; fixed with `mega_cluster_prevention` gate that checks proposed merge target size before committing
- **MCP process score events silently skipped** — `TaxonomyEventLogger` was not initialized in MCP process lifespan, causing `get_event_logger()` to raise `RuntimeError` and drop all score events. Fixed by initializing the singleton in MCP lifespan
- **Score events not reaching SSE** — MCP→backend cross-process notification was missing `cross_process=True` flag and ring buffer mirroring. Both issues fixed
- **`/api/clusters/activity` returned 404** — route was registered after the `{cluster_id}` dynamic route, causing FastAPI to capture `activity` as a cluster ID. Moved to before the dynamic route
- **Activity panel JSONL history fallback** — panel showed 0 events after server restart because ring buffer was empty; now seeds from JSONL history when ring buffer is empty
- **Split events logged wrong path** — `log_path` argument was not propagated through call chain; warm-path splits logged `path="cold"`. Fixed with parameterized `log_path`
- **Duplicate merge-skip events** — per-node logging in both merge passes produced event storms; consolidated to per-phase summary events
- **No-op phase events suppressed** — events for phases with no mutations are now suppressed when the system has converged, reducing noise in the Activity panel

## v0.3.12-dev — 2026-04-03

### Added
- **Taxonomy engine observability** — `TaxonomyEventLogger` service dual-writes structured decision events to JSONL files (`data/taxonomy_events/`) and in-memory ring buffer (500 events). 17 instrumentation points across hot/warm/cold paths with 12 operation types and 24 decision outcomes
- **Cluster activity endpoints** — `GET /api/clusters/activity` (ring buffer with path/op/errors-only filters) and `GET /api/clusters/activity/history` (paginated JSONL by date). Routed before `{cluster_id}` to prevent shadowing
- **`taxonomy_activity` SSE event type** — streams decision events to frontend in real time
- **ActivityPanel.svelte** — collapsible bottom panel below 3D topology. Filter chips for path (hot/warm/cold), 12 operation types, errors-only toggle. Color-coded decision badges. Expandable context grid. Cluster click-through. Pin-to-newest auto-scroll. Seeds from ring buffer with JSONL history fallback after server restart
- **Sub-domain discovery** — `_propose_sub_domains()` uses HDBSCAN to discover semantic sub-groups within oversized domains (≥20 members, mean coherence <0.50). Sub-domains are domain nodes with `parent_id` pointing to parent domain, same guardrails as top-level domains. Label format: `{parent}-{qualifier}`. Counts toward 30-domain ceiling. Parallel Haiku label generation via `asyncio.gather`
- **`DomainResolver.add_label()`** — runtime domain cache registration after sub-domain creation
- **`RetireResult` dataclass** — replaces boolean return from `attempt_retire()`, captures sibling target, families reparented, optimizations reassigned
- **`PhaseResult.split_attempted_ids`** — tracks clusters with attempted splits regardless of outcome, for post-rejection metadata persistence
- **Split/sub-domain constants** — `SPLIT_MERGE_PROTECTION_MINUTES` (60 min), `SUB_DOMAIN_MIN_MEMBERS` (20), `SUB_DOMAIN_COHERENCE_CEILING` (0.50), `SUB_DOMAIN_MIN_GROUP_MEMBERS` (5)
- **`compute_score_correlated_target()`** — score-weighted optimal weight profile from optimization history using z-score contribution weighting
- **Few-shot example retrieval** — optimizer prompt includes 1-2 before/after examples from high-scoring similar past optimizations (cosine ≥0.50, score ≥7.5)
- **Score-informed strategy recommendation** — `recommend_strategy_from_history()` overrides "auto" fallback with data-driven strategy selection
- **`OptimizedEmbeddingIndex`** — in-memory cosine search for per-cluster mean optimized-prompt embeddings
- **`resolve_contextual_weights()`** — per-phase weight profiles from task type + cluster learned weights
- **Output coherence** — pairwise cosine of optimized_embeddings within clusters, stored in `cluster_metadata["output_coherence"]`
- **`blend_embeddings()` and `weighted_blend()`** — shared multi-embedding blending in `clustering.py`

### Changed
- **Multi-embedding HDBSCAN** — warm/cold paths now use blended embeddings (0.65 raw + 0.20 optimized + 0.15 transformation). Hot-path stays raw-only
- **Parallel split label generation** — `split_cluster()` restructured into 3 phases: collect data (sequential DB), `asyncio.gather` label generation (parallel LLM), create objects (sequential). Reduces split from ~7 min to ~17s
- **Deferred pattern extraction** — meta-pattern extraction removed from `split_cluster()`, children marked `pattern_stale=True` for warm-path Phase 4 (Refresh). Eliminates 15+ sequential Haiku calls from critical split path
- **Parallel Phase 4 label generation** — `phase_refresh()` restructured with `asyncio.gather` for all stale cluster labels
- Split merge protection window increased from 30 minutes to 60 minutes — prevents same-domain merge from immediately undoing cold-path splits
- **Score-correlated batch adaptation** — replaces per-feedback weight adaptation in warm path
- **Composite fusion Signal 3** — upgraded to `OptimizedEmbeddingIndex` lookup
- **Few-shot retrieval** — upgraded to dual-retrieval (input + output similarity)
- **Split/merge heuristics** — split considers output coherence; merge uses output coherence boost

### Fixed
- **Groundhog Day split loop (variant 1)** — `split_failures` metadata lost on Q-gate transaction rollback, causing same cluster to be split and rejected indefinitely. Fixed with post-rejection metadata persistence in a separate committed session
- **Groundhog Day split loop (variant 2)** — 30-minute merge protection expired before warm path ran, causing split children to be immediately re-merged. Fixed by increasing protection to 60 minutes
- **`/api/clusters/activity` returned 404** — route was after `{cluster_id}` dynamic route; moved before it
- **Activity panel showed 0 events after restart** — added JSONL history fallback when ring buffer is empty
- **Merge-skip event storms** — per-node logging in both merge passes consolidated to summary events
- **Split events logged wrong path** — parameterized via `log_path` argument
- **`errors_only` filter inconsistency** — frontend and backend now both check `op="error"` + `decision in (rejected, failed, split_failed)`
- **Event `{#each}` key collisions** — added cluster_id + index for uniqueness in ActivityPanel
- **`keyMetric()` wrong data for `create_new`** — gated display by decision type
- **Activity toggle routed through store** — uses `clustersStore.toggleActivity()` instead of local state
- **SSE events flow through store directly** — removed window CustomEvent indirection
- Cold path epsilon references constant instead of magic number
- `context: dict = Field(default_factory=dict)` replaces mutable default in schema
- `OptimizedEmbeddingIndex` stale entries removed during all lifecycle operations

## v0.3.11-dev — 2026-04-02

### Added
- **Unified embedding architecture** — 3-phase system (cross-cluster injection, multi-embedding foundation, composite fusion) enhancing taxonomy search with multi-signal queries
- Cross-cluster pattern injection: universal meta-patterns flow across topic boundaries ranked by composite relevance (`cosine_similarity × log2(1 + global_source_count) × cluster_avg_score_factor`)
- `MetaPattern.global_source_count` field tracking cross-cluster presence, computed during warm-path refresh via pairwise cosine similarity (threshold 0.82)
- `Optimization.optimized_embedding` and `Optimization.transformation_embedding` columns for optimized prompt embeddings and L2-normalized improvement direction vectors
- `Optimization.phase_weights_json` column persisting the weight profile used for each optimization, enabling feedback-driven adaptation
- `PromptCluster.weighted_member_sum` column for score-weighted centroid computation
- `TransformationIndex` module — in-memory technique-space search index with `get_vector()`, snapshot/restore, running-mean upsert, mirroring `EmbeddingIndex` API
- `CompositeQuery` and `PhaseWeights` dataclasses for multi-signal fusion with per-phase weight profiles (analysis, optimization, pattern_injection, scoring)
- `resolve_fused_embedding()` shared helper consolidating composite query construction, weight loading, and fusion
- `adapt_weights()` EMA convergence toward successful weight profiles on positive feedback; `decay_toward_defaults()` drift back per warm cycle
- `cross_cluster_patterns` field on `MatchOutput` MCP schema — `synthesis_match` now returns universal techniques alongside topic-matched patterns
- One-time backfill migration for existing optimization embeddings with `data/.embedding_backfill_done` marker
- Constants: `CROSS_CLUSTER_MIN_SOURCE_COUNT`, `CROSS_CLUSTER_MAX_PATTERNS`, `CROSS_CLUSTER_RELEVANCE_FLOOR`, `CROSS_CLUSTER_SIMILARITY_THRESHOLD`, `FUSION_CLUSTER_LOOKUP_THRESHOLD`, `FUSION_PATTERN_TOP_K`

### Changed
- `auto_inject_patterns()` uses composite fusion for cluster search instead of raw prompt embedding alone
- `match_prompt()` uses composite fusion for similarity search with relevance-scored cross-cluster patterns
- `context_enrichment._resolve_patterns()` includes cross-cluster patterns for passthrough tier
- `assign_cluster()` centroid update uses score-weighted running mean instead of equal-weight mean
- Warm-path centroid reconciliation uses per-member score-weighted mean from ground truth
- Cold-path `weighted_member_sum` recomputed from true per-member scores instead of average approximation
- `TransformationIndex` maintained across all lifecycle operations: hot path (running mean upsert), merge (remove loser), retire (remove), split (remove archived), zombie cleanup (remove), cold path (full rebuild), speculative rollback (snapshot/restore)
- Feedback-driven phase weight adaptation wired end-to-end: positive feedback shifts weights toward the stored optimization-time profile via EMA

### Fixed
- Adaptation loop was dead code — `update_phase_weights()` never called from feedback flow
- Adaptation loop was no-op even when wired — both `current` and `successful` loaded from same preferences file; fixed by storing weight snapshot on Optimization record
- Cross-cluster relevance formula in `match_prompt()` was missing `cluster_score_factor` (inconsistent with `auto_inject_patterns()`)
- Hardcoded magic numbers in `fusion.py` Signal 4 replaced with named constants
- Silent `except: pass` blocks in engine.py and warm_phases.py now log at debug level

### Added
- `cold_path.py` module with `execute_cold_path()` and `ColdPathResult` — extracted cold path from engine.py with quality gate via `is_cold_path_non_regressive()` to reject regressive HDBSCAN refits instead of committing unconditionally
- `warm_path.py` orchestrator module with `execute_warm_path()` — sequential 7-phase warm path with per-phase Q gates, embedding index snapshot/restore on speculative rollback, per-phase deadlock breaker counters, and `WarmPathResult` aggregated dataclass
- `warm_phases.py` module extracting 7 warm-path phase functions from engine.py monolith — reconcile, split_emerge, merge, retire, refresh, discover, audit — each independently callable with dependency-injected engine and fresh AsyncSession
- `PhaseResult`, `ReconcileResult`, `RefreshResult`, `DiscoverResult`, `AuditResult` dataclasses for structured phase return values

### Changed
- `engine.py` refactored to delegate warm and cold path execution to new modules — removed `_run_warm_path_inner()` (~1075 lines) and `_run_cold_path_inner()` (~455 lines), reducing engine.py from 3587 to 2049 lines
- `run_warm_path()` now accepts `session_factory` (async context manager factory) instead of a single `db` session, enabling per-phase session isolation
- `run_cold_path()` now delegates to `execute_cold_path()` from cold_path.py
- `WarmPathResult` and `ColdPathResult` dataclasses moved from engine.py to warm_path.py and cold_path.py respectively, with extended schemas (q_baseline/q_final/phase_results and q_before/q_after/accepted)
- Added `_phase_rejection_counters` dict attribute to TaxonomyEngine for per-phase deadlock tracking

### Fixed
- Cold path now excludes archived clusters from HDBSCAN input — original used `state != "domain"` which included archived (fix #5)
- Cold path existing-node matching now includes mature/template states — original used `state.in_(["active", "candidate"])` which missed them (fix #6)
- Cold path resets `split_failures` metadata on matched nodes after HDBSCAN refit (fix #14)
- Warm-path reconciliation now queries fresh non-domain/non-archived nodes instead of iterating a stale `active_nodes` list (fixes #10, #16)
- Emerge phase excludes domain/archived nodes from orphan family query (fix #7)
- Leaf split now increments `ops_accepted` counter on success (fix #9)
- Noise reassignment uses pre-fetched embedding cache instead of per-point DB queries (fix #11)
- Replaced 3 manual cosine similarity calculations with `cosine_similarity()` from clustering.py (fix #12)
- `warm_path_age` now increments unconditionally in audit phase (fix #13)
- Stale label/pattern refresh now extracts new patterns before deleting old ones, preventing data loss on extraction failure (fix #15)

### Added
- `routing_tier` column on Optimization model — persists which tier (internal/sampling/passthrough) processed each optimization, with startup backfill for legacy records
- `routing_tier` field in `OptimizationDetail`, `PipelineResult`, and `HistoryItem` API responses
- Inspector Tier row showing persisted routing tier with color coding (green=sampling, cyan=internal, yellow=passthrough)
- `last_model` attribute on `LLMProvider` base class — providers now report the actual model ID from each LLM response
- Status bar tier badge now derives from the active optimization's persisted tier when viewing history

### Fixed
- Inspector panel now shows correct provider, model, and per-phase model IDs for sampling-originated optimizations — previously displayed internal pipeline defaults
- Internal pipeline now captures actual model IDs from provider responses instead of using preference aliases for `models_by_phase`
- Event bus race guard prevents duplicate `loadFromRecord()` when both SSE proxy and event bus deliver the same sampling result
- Re-parenting sweep in domain discovery now parses `domain_raw` values via `parse_domain()` before counting — qualified strings like `"Backend: Security"` now correctly match lowercased domain node labels instead of silently failing to reparent
- `attempt_merge` now reconciles survivor's `scored_count` and `avg_score` immediately from both nodes' weighted contributions instead of deferring to warm-path reconciliation
- `attempt_retire` now reconciles target sibling's `scored_count` and `avg_score` when optimizations are reassigned, matching the merge hardening pattern
- Leaf split noise reassignment now updates sub-cluster `avg_score` with running mean instead of only incrementing `scored_count`
- Removed redundant `get_engine()` call in `attempt_retire` — embedding index removal is already handled by the engine caller, and the inline call broke dependency injection
- Unified archival field clearing across all 5 archival paths (merge loser, retire, leaf split, zombie cleanup, reassign_all) — `usage_count` and `scored_count` were missing from some paths, causing phantom data in archived clusters
- Added missing `archived_at` timestamp in `reassign_all_clusters()` archival — was the only path that didn't set the timestamp
- Unified naive UTC timestamps across `lifecycle.py` and `engine.py` via `_utcnow()` — SQLAlchemy `DateTime()` strips tzinfo on round-trip, so aware datetimes caused comparison safety issues with `prompt_lifecycle.py` curation
- Pipeline usage increment now has atomic SQL fallback matching sampling_pipeline robustness — prevents silent usage loss when `increment_usage()` fails
- Removed 3 redundant inline imports in `engine.py` (`parse_domain`, `extract_meta_patterns`, `merge_meta_pattern`) already present at top-level
- Removed unused `datetime`/`timezone` imports in `_suggest_domain_archival` after `_utcnow()` migration
- Domain promotion (`POST /api/domains/{id}/promote`) now sets `promoted_at` timestamp and clears `parent_id` (domain nodes are roots)
- Retire lifecycle operation no longer double-counts `member_count` on the target sibling — child cluster re-parenting now correctly avoids inflating the Optimization-based member_count
- `usage_count` increment is now atomic via SQL `UPDATE ... SET usage_count = usage_count + 1`, preventing lost writes under concurrent optimization completions (including sampling pipeline fallback path)
- Fixed mutable default aliasing in `read_meta()` — `signal_keywords` list default is now shallow-copied to prevent cross-call contamination
- Fixed tooltip timer race condition — `setTimeout` callback now guards against firing after `destroy()`, eliminating the `ActivityBar.test.ts` error

### Added
- `ClusterMeta` TypedDict and `read_meta()`/`write_meta()` helpers for type-safe `cluster_metadata` access — replaces scattered `node.cluster_metadata or {}` pattern with coerced defaults
- `get_injection_stats()` function and `injection_stats` field on health endpoint — surfaces pattern injection provenance success/failure counts for operational monitoring
- Frontend `HealthResponse` interface updated with `injection_stats` field for contract parity

### Changed
- Extracted `merge_score_into_cluster()` and `combine_cluster_scores()` helpers in `family_ops.py` — replaces 4 duplicated score reconciliation patterns across assign_cluster, attempt_merge, attempt_retire, and noise reassignment
- `attempt_merge` accepts `embedding_svc` parameter for dependency injection instead of instantiating `EmbeddingService()` per merge; all 3 engine call sites now pass the singleton
- Removed dead `tree_state` parameter from `create_snapshot()` — column was serialized but never deserialized for recovery
- Consolidated 9 scattered inline `cluster_meta` imports in `engine.py` to single top-level import
- Pattern injection provenance: `auto_inject_patterns()` now persists `OptimizationPattern` records with `relationship="injected"` recording which clusters influenced each optimization
- `GET /api/clusters/injection-edges` endpoint returning directed weighted edges aggregated by (source cluster, target cluster) with archived-cluster filtering
- Injection edge visualization in 3D topology: warm gold/amber directed edges with weight-proportional opacity (0.15-0.50), controlled by "Injection" toggle in TopologyControls
- Similarity edge layer for 3D topology visualization: `GET /api/clusters/similarity-edges` endpoint + frontend toggle overlay with dashed neon-cyan lines (opacity proportional to cosine similarity)
- `EmbeddingIndex.pairwise_similarities()` method for batch cosine similarity computation from the L2-normalized centroid matrix
- `interpolate_position()` in `projection.py` — cosine-weighted sibling interpolation for UMAP coordinates between cold path runs
- Hot-path position interpolation: new clusters created by `assign_cluster()` inherit interpolated UMAP positions from positioned siblings in the same domain
- Warm-path position interpolation: child clusters from `attempt_split()` placed at parent position + random 2.0-unit radial offset
- Visual quality encoding in 3D topology: wireframe brightness mapped to cluster coherence [0,1], fill color saturation mapped to avg_score [1,10], with legend tooltip in controls

## v0.3.10-dev — 2026-04-01

### Added
- Adaptive merge threshold for cluster assignment: `BASE_MERGE_THRESHOLD=0.55 + 0.04 * log2(1 + member_count)` — replaces static 0.78 that blocked all legitimate merges while allowing centroid-drift mega-clusters
- Task-type mismatch penalty (-0.05 cosine) during cluster merge — soft signal that prevents mixed-type clusters without hard-blocking
- `semantic_upgrade_general()` post-LLM classification gate — upgrades `task_type="general"` when strong keywords are present (e.g., "implement"→coding, "analyze"→analysis)
- `POST /api/clusters/reassign` endpoint — replays hot-path cluster assignment for all optimizations with current adaptive threshold
- `POST /api/clusters/repair` endpoint — rebuilds orphaned join records, meta-patterns, coherence, and member_count in one operation
- `repair_data_integrity()` engine method covering 4 repair tasks: join table, meta-patterns, coherence computation, member_count reconciliation
- Cluster task_type auto-recomputation as statistical mode of members after each merge (>50% majority required)
- Hot-path old-cluster decrement — when optimization is reassigned, old cluster's member_count/scored_count is decremented
- Cold path: domain nodes excluded from HDBSCAN input, self-reference prevention, post-HDBSCAN domain-link restoration, member_count reconciliation from Optimization rows
- Autoflush disabled on read-only cluster endpoints (tree, stats, detail) — prevents 500 during concurrent recluster
- Embedding index disk cache (`data/embedding_index.pkl`) with 1-hour TTL — skips DB rebuild on server restart when cache is fresh
- Adaptive warm-path interval via `WARM_PATH_INTERVAL_SECONDS` setting — warm path runs early when `taxonomy_changed` fires instead of always waiting the full interval
- Semantic gravity n-body force simulation with 5 forces: UMAP anchor, parent-child spring, same-domain affinity, universal repulsion, collision resolution
- Domain node visual overhaul: dodecahedron geometry with EdgesGeometry pentagonal outlines, vertex anchor points, slow Y-axis rotation
- Pattern graph reactive to navigator state filter tabs (clicking active/archived/template filters the 3D graph)
- Same-domain duplicate merge detection: two-signal system (label match + centroid >0.40, same-domain embedding >=0.65)
- Warm-path stale label/pattern refresh when cluster grows 3x+ since last extraction
- Warm-path member count + coherence reconciliation from actual Optimization rows
- Warm-path zombie cluster cleanup (archives 0-member clusters, clears stale usage)
- Warm-path post-discovery re-parenting sweep for general-domain stragglers
- Tree integrity checks #6 (non-domain parents) and #7 (archived with usage) with auto-repair
- `InjectedPattern` dataclass with cluster_label, domain, similarity metadata
- `format_injected_patterns()` shared utility (eliminates pipeline duplication)
- `StateFilter` type and `filteredTaxonomyTree` derived on cluster store
- Enhanced injection chain observability: cluster names, domains, similarity scores in logs
- Embedding index top-score diagnostic logging on search miss
- Root logger configuration for app.services.* INFO propagation
- Score dimension CSS Grid alignment with column headers (score/delta/orig)
- Navigator column headers (MBR/USE/SCORE) outside scrollable area (sticky)
- Domain dots enlarged 6px to 8px with inset box-shadow contrast ring

### Changed
- Cluster merge threshold: static 0.78 replaced with adaptive formula that grows with cluster size (0.59 at 1 member → 0.71 at 14 members) — empirical analysis showed only 4/1711 prompt pairs exceeded 0.78
- Heuristic analyzer: `build` keyword weight raised 0.5→0.7, `calculate` (0.6) added to coding signals
- Warm-path merge uses adaptive threshold (was static 0.78)
- Cold-path cluster matching uses adaptive threshold (was static 0.78)
- Cold-path no longer overwrites member_count with HDBSCAN group size — reconciles from Optimization rows
- `attempt_merge()` zeros loser's member_count/scored_count/avg_score on archival (matches `attempt_retire()`)
- `attempt_retire()` increments target_sibling.member_count by reassigned optimization count
- Data domain seed color changed from #06b6d4 (teal) to #b49982 (warm taupe) — was perceptually identical to database #36b5ff (ΔE=0.068→0.200)
- PROVEN TEMPLATES section visible in active tab (was only visible in "all" tab)
- Auto-inject threshold lowered 0.72 to 0.45 for broad post-merge centroids
- Domain discovery thresholds: MIN_MEMBERS 5 to 3, MIN_COHERENCE 0.6 to 0.3
- Domain node size multiplier 2.5x to 1.6x (aggregate child-member sizing makes 2.5x overkill)
- Domain nodes aggregate children's member_count for sizing (not own member_count)
- Domains sorted by cluster count descending in navigator (most populated first)
- Navigator badge reflects filtered view count, not raw total
- Unarchive button hidden for 0-member clusters
- Promote button: removed pointer-events:none from disabled state (was blocking tooltip)
- Extract patterns prompt: domain-aware extraction replaces framework-agnostic directive
- Optimizer prompt: precision pattern application with per-pattern relevance evaluation
- `attempt_merge()` now reassigns Optimizations and MetaPatterns from loser to survivor
- Linked optimizations query uses Optimization.cluster_id instead of OptimizationPattern join table
- TopologyControls node counts computed from filteredTaxonomyTree (respects state filter)
- Inspector clears selection on state filter tab change

### Fixed
- Cold path HDBSCAN destroyed domain→cluster parent links (32 self-references, 7 missing parents per recluster) — domain nodes now excluded from HDBSCAN, self-references prevented, domain links restored post-HDBSCAN
- Cold path set member_count from HDBSCAN group size instead of actual Optimization count — inspector showed "Members: 10" but only 4 linked optimizations
- SQLAlchemy autoflush race condition: concurrent recluster + cluster detail GET caused 500 errors
- 4 of 6 "general" task_type prompts were misclassified — LLM returned "general" for prompts with explicit coding/analysis keywords
- Hierarchical topology edges invisible when parent domain node was at LOD visibility boundary
- ClusterNavigator default tab test failures (5 pre-existing) — tests expected "all" default but implementation uses "active"
- Auto-injected cluster IDs now included in usage_count increment (was missing from internal pipeline)
- Coherence recomputation from actual member embeddings (cold path left values at 0.0)
- Organic domain discovery blocked by uncomputed coherence
- Self-referencing parent_id cycles (3 detected and repaired)
- 28 non-domain parent relationships repaired (clusters parented under other clusters instead of domain nodes)
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` (deprecated Python 3.12+)
- Domain highlight dimming preserves domain node EdgesGeometry outlines (userData.isInterClusterEdge marker)
- Same-domain merge breaks after first merge per domain group per cycle (prevents stale-centroid reads)
- Removed duplicate DEBUG log in increment_usage
- Navigator pluralization fixes (1 member/cluster singular)
- Topology test updated for removed similarity edges

## v0.3.8-dev — 2026-03-29

### Added
- Column headers (Name/Members/Used/Score) above cluster family rows in ClusterNavigator
- Mid-LOD label visibility for large clusters (5+ members) and domain nodes in topology graph
- Domain wireframe ring (1.3x outer contour) differentiating domain hub nodes in topology
- Score-based size variation for GENERAL domain nodes in topology graph
- Optimization timestamps in Inspector linked optimizations list
- Domain highlight interaction: click domain header in navigator to dim non-matching nodes in graph
- `highlightedDomain` state and `toggleHighlightDomain()` method on cluster store
- `setVisibleFor()` method on TopologyLabels for per-node label visibility control
- Unified domain taxonomy — domains are now first-class taxonomy nodes discovered organically from user behavior (ADR-004)
- `GET /api/domains` endpoint for dynamic domain palette
- `POST /api/domains/{id}/promote` for manual cluster-to-domain promotion
- Warm-path domain discovery with configurable thresholds (5+ members, coherence ≥0.6, ≥60% consistency)
- Domain stability guardrails: color pinning, retire exemption, merge approval gate, coherence floor (0.3), split creates candidates
- Tree integrity verification with 5 checks and auto-repair (orphans, mismatches, persistence, self-refs, duplicates)
- Domain count and ceiling (30) in health endpoint with frontend amber warning at 80%
- Risk detection: signal staleness tracking, general domain stagnation monitor, domain archival suggestions
- `DomainResolver` service — cached domain label lookup from DB, process-level singleton
- `DomainSignalLoader` service — dynamic heuristic keyword signals from domain node metadata
- `cluster_metadata` JSON column on `PromptCluster` for domain node configuration
- Partial unique index `uq_prompt_cluster_domain_label` for DB-level domain label uniqueness
- Frontend domain store (`domains.svelte.ts`) with SSE-driven invalidation
- Stats endpoint extended with `q_trend`, `q_current`, `q_min`, `q_max`, `q_point_count`
- Stats cache with 30s TTL, invalidated on warm/cold path completion
- ScoreSparkline enhanced: configurable dimensions, baseline overlay, hover tooltips, per-dimension view
- Inspector Q health sparkline with trend indicator (improving/stable/declining)
- Inspector per-dimension score overlay (AVG/DIM toggle) for refinement sessions
- `trendInfo()` and `parsePrimaryDomain()` formatting utilities

### Changed
- Lowered auto-inject cosine threshold from 0.72 to 0.60 and increased candidate count from 3 to 5 for broader pattern matching
- Enriched auto-injected patterns with structured metadata (domain, similarity score, source cluster label) in optimizer context
- Replaced generic meta-pattern instruction in optimizer prompt with precision application block requiring per-pattern evaluation and an Applied Patterns summary section
- Added diagnostic logging for empty embedding index and zero-match scenarios in pattern injection
- Domain headers in ClusterNavigator use display font (Syne) at 10px/700 weight with 0.1em letter-spacing
- Usage count badge uses conditional teal color when count > 0 (replaces uniform badge-neon styling)
- Domain size multiplier increased from 2.0x to 2.5x in topology graph
- Removed same-domain similarity edges from topology graph for cleaner visual hierarchy
- Promote to Template button gated: requires 3+ members or 1+ pattern usage
- Usage metric row in Inspector shows explanatory tooltip on hover
- Analyzer prompt template uses dynamic `{{known_domains}}` variable instead of hardcoded list
- `taxonomyColor()` resolves from API-driven domain store instead of compile-time map
- Inspector domain picker loads domains dynamically from API
- StatusBar shows domain count with amber warning at 80% ceiling
- Topology renders domain nodes at 2x size with warm amber state color
- Heuristic analyzer domain classification driven by `DomainSignalLoader` (database-backed keywords)
- Domain lifecycle: emerge inherits majority domain, split inherits parent domain
- `DomainResolver.resolve()` signature simplified (removed unused `db` parameter)

### Removed
- `VALID_DOMAINS` constant from `pipeline_constants.py`
- `apply_domain_gate()` function from `pipeline_constants.py`
- `_DOMAIN_SIGNALS` hardcoded dict from `heuristic_analyzer.py`
- `DOMAIN_COLORS` hardcoded map from `colors.ts`
- `KNOWN_DOMAINS` hardcoded array from `Inspector.svelte`

## v0.3.7-dev — 2026-03-28

### Added
- Added `parse_domain()` utility in `app/utils/text_cleanup` for "primary: qualifier" domain format parsing
- Added multi-dimensional domain classification — LLM analyze prompt and heuristic analyzer output "primary: qualifier" format (e.g., "backend: security") when cross-cutting domains detected
- Added zero-LLM heuristic suggestions via `generate_heuristic_suggestions()` — 3 deterministic suggestions (score/analysis/strategy) for passthrough tier, 18 unit tests
- Added structural meta-pattern extraction via `extract_structural_patterns()` — score delta + regex detection, passthrough results now contribute patterns to taxonomy
- Added `heuristic_flags` JSON column to Optimization model for score divergence persistence across all tiers
- Added `suggestions` JSON column to Optimization model — persisted for all tiers (was only streamed via SSE, never stored)
- Added `was_truncated` field to MCP `PrepareOutput` schema
- Added `title_case_label()` utility with acronym preservation (API, CSS, JWT, etc.)
- Added `docs/ROADMAP.md` — project roadmap with planned/exploring/deferred/completed sections
- Added Inspector suggestions section for all tiers (score/analysis/strategy labels)
- Added Inspector changes section with MarkdownRenderer (was flat text)
- Added Inspector metadata: duration, domain, per-phase models for internal tier
- Added Pattern Graph same-domain edges connecting related clusters
- Added Pattern Graph always-visible labels for small graphs (≤ 8 nodes)
- Added Pattern Graph UMAP position scaling (10x) for proper node spread

### Changed
- Domain colors overhauled to electric neon palette with zero tier accent overlap: backend=#b44aff, frontend=#ff4895, database=#36b5ff, security=#ff2255, devops=#6366f1, fullstack=#d946ef
- Pattern Graph nodes use sharp wireframe contour over dark fill (brand zero-effects directive)
- Domain color priority: domain name takes precedence over OKLab color_hex in Pattern Graph
- LOD thresholds lowered (far=0.4, mid=0.2) so default clusters visible before cold-path recluster
- Taxonomy merge prevention compares primary domain only (ignores qualifier)
- Frontend `taxonomyColor()` parses "primary: qualifier" format and does keyword matching for free-form strings
- Passthrough text cleanup runs before heuristic scoring (was after — scores reflected uncleaned preambles)
- Strategy learning now includes validated passthrough results (thumbs_up feedback via correlated EXISTS subquery)
- Passthrough guide step 6 updated to mention suggestions; feature matrix Suggestions row changed ✗ → ✓
- Intent labels title-cased at all persistence boundaries for display consistency across tiers

### Fixed
- Fixed CI lockfile: regenerated with Node 24 for cross-platform optional dependencies
- Fixed 3 frontend tests: PassthroughGuide (TierGuide refactor), forge SSE error (traceId), MarkdownRenderer (Svelte 5 comments)
- Fixed passthrough output length validation (MAX_RAW_PROMPT_CHARS) in both MCP and REST save paths
- Fixed `DATA_DIR` import-time capture in optimize.py router — tests read real preferences instead of fixture
- Fixed cluster detail loading stuck indefinitely — generation counter race in `_loadClusterDetail` finally block
- Fixed cluster skeleton buffering after Inspector dismiss — sync ClusterNavigator expandedId with store
- Fixed Pattern Graph nodes invisible — LOD thresholds too high for default persistence (0.5)
- Fixed wrong onboarding modal on startup — gated tier guide trigger on preferences load completion
- Fixed startup toggle auto-sync race — deferred reconciliation to after both health AND preferences loaded

## v0.3.6-dev — 2026-03-27

### Fixed
- Fixed 6 routing tier bugs caused by per-session RoutingManager replacement — RoutingManager is now a process-level singleton guarded by `_process_initialized` flag
- Fixed lifespan exit nullifying `_shared._routing` — per-session cleanup removed entirely; singletons survive all Streamable HTTP sessions
- Fixed `_clear_stale_session()` racing with middleware writes — moved to `__main__` (process startup) only
- Fixed `_inspect_initialize` guard bypass after RoutingManager replacement — added secondary check via `_sampling_sse_sessions` (class-level, survives startup races)
- Fixed `on_mcp_disconnect()` clearing `mcp_connected` when only the sampling bridge disconnected — new `on_sampling_disconnect()` clears only `sampling_capable`, keeps `mcp_connected=True`
- Fixed `disconnect_averted` pattern firing every 60s when only non-sampling clients connected

### Added
- Added `on_sampling_disconnect()` to RoutingManager — differentiates partial (bridge leaves) vs full (all clients leave) disconnect
- Added dual-layer guard in `_inspect_initialize`: primary (RoutingManager state) + secondary (`_sampling_sse_sessions`) prevents non-sampling clients from overwriting sampling state
- Added 6 unit tests for `on_sampling_disconnect` (state, events, idempotency, tiers, persistence, chained disconnect)
- Added `backend/CLAUDE.md` — routing internals (singleton pattern, tier decision, state transitions, middleware guard logic, disconnect signals) + sampling pipeline internals (fallback chain, free-text vs JSON phases, text cleaning, bridge workaround, passthrough workflow, monkey patches)
- Added `docs/routing-architecture.md` — comprehensive routing reference with ASCII diagrams, state machine, multi-client coordination, disconnect detection, cross-process communication, persistence/recovery, common scenarios, failure modes
- Exposed VS Code bridge source and sampling config to remote (`.vscode/settings.json`, `VSGithub/mcp-copilot-extension/` source files)

### Changed
- Sampling capability detection section in CLAUDE.md rewritten to reflect singleton pattern, dual-layer guard, and two disconnect signals

## v0.3.5-dev — 2026-03-26

### Added
- Added MCP Copilot Bridge VS Code extension with dynamic tool discovery, sampling handler, health check auto-reconnect, roots/list support, and phase-aware schema injection
- Added `canBeReferencedInPrompt` + `languageModelToolSets` for all 11 MCP tools in bridge manifest — enables Copilot agent mode visibility
- Added REST→MCP sampling proxy with SSE keepalive (10s heartbeat) for web UI sampling when Force IDE Sampling is ON
- Added event bus auto-load: frontend loads sampling results via `/api/events` SSE when `/api/optimize` stream drops
- Added deep workspace scanning: README.md (80 lines), entry point files (40 lines × 3), architecture docs (60 lines × 3) injected alongside guidance files
- Added `McpError` catch in `_sampling_request_structured` — VS Code MCP client throws McpError (not TypeError) when tool calling is unsupported
- Added JSON schema injection in sampling text fallback — when tool calling fails, JSON schema appended to user message
- Added JSON terminal directive to scoring system prompt (sampling only) — forces JSON output from IDE LLM
- Added `strip_meta_header` (in `app/utils/text_cleanup`): strips LLM preambles ("Here is the optimized prompt..."), code fence wrappers, meta-headers, trailing orphaned `#`
- Added `split_prompt_and_changes` (in `app/utils/text_cleanup`): separates LLM change rationale from optimized prompt via 14 marker patterns
- Added `_build_analysis_from_text`: keyword-based task_type/domain/intent extraction from free-text LLM responses with confidence scaling
- Added sampling downgrade prevention — non-sampling MCP clients no longer overwrite `sampling_capable=True` set by the bridge
- Added `sync-tools.js` build script for bridge extension — queries MCP server `tools/list` and generates `package.json` manifest
- Added `VALID_DOMAINS` whitelist in `pipeline_constants.py` — shared across MCP and REST passthrough handlers

### Changed
- Optimize template: unconditionally anchors to workspace context (removed conditional "If the original prompt references a codebase")
- Optimize template: strategy takes precedence over conciseness rule when they conflict (fixes chain-of-thought/role-playing dissonance)
- Optimize template: evaluates weaknesses with judgment instead of blind obedience to analyzer checklist
- Optimize template: changes summary requires rich markdown format (table, numbered list, or nested bullets)
- Codebase context now available for ALL routing tiers when repo linked (was passthrough-only)
- All 4 enrichment call sites default `workspace_path` to `PROJECT_ROOT` when not provided
- Scoring `max_tokens` capped to 1024 for sampling (was 16384 — prevented LLM timeout from verbose chain-of-thought)
- Heuristic clarity: clamp Flesch to [0, 100] before mapping + structural clarity bonus for headers/bullets
- Inspector shows Analyzer/Optimizer/Scorer models on separate rows (was single crammed line)
- Navigator SYSTEM card Scoring row shows actual model ID dynamically (was hardcoded "hybrid (via IDE)")
- ForgeArtifact section title uses `--tier-accent` color (was static dim)
- Bridge sampling handler: phase-aware schema injection (JSON schema only for analyze/score, free-text for optimize/suggest)
- Passthrough template: per-dimension scale anchors, anti-inflation guidance, domain/intent_label fields in JSON spec
- Hardened cookie security: SameSite=Lax, environment-gated Secure flag, /api path scope, 14-day session lifetime

### Fixed
- Sampling score phase: caught `McpError` in structured request fallback (VS Code throws McpError, not TypeError)
- Sampling score phase: `run_sampling_analyze` parity — added fallback error handling + JSON directive + max_tokens cap
- UI stale after sampling: event bus auto-load fires for ALL forge statuses (was only analyzing/optimizing/scoring)
- UI horizontal scroll: `min-width: 0` across full flex/grid layout chain (layout → EditorGroups → ForgeArtifact → MarkdownRenderer)
- LLM code fence wrapper: frontend + backend strip `\`\`\`markdown` wrapping, preamble sentences, trailing `\`\`\``, orphaned `#`
- Sampling state race: non-sampling client `initialize` no longer clears `sampling_capable=True` from bridge
- Heuristic scorer: clamped Flesch to [0, 100] (technical text went negative → clarity=1.5)
- SemanticTopology: `untrack()` on sceneData write prevents `effect_update_depth_exceeded`
- Inspector: `dedupe()` on keyed each blocks prevents `each_key_duplicate` Svelte errors
- Clamped external passthrough scores to [1.0, 10.0] before hybrid blending
- Excluded `hybrid_passthrough` from z-score historical stats to prevent cross-mode contamination
- Normalized heuristic scorer clamping to consistent `max(1.0, min(10.0, score))` pattern
- Fixed contradictory scoring instructions in passthrough template
- Added domain validation against whitelist in passthrough save (invalid domains fall back to "general")
- Added `intent_label` 100-character cap in passthrough save
- Added workspace path safety validation in MCP prepare handler (blocks system directories)
- Added anti-inflation guidance and structured metadata fields (`domain`, `intent_label`) to passthrough template
- Added 16 new passthrough audit tests (domain validation, intent_label cap, SSE format, heuristic clamping, constant identity)
- Added environment-gated MCP server authentication via bearer token (ADR-001)
- Added PBKDF2-SHA256 key derivation with context-specific salts (ADR-002)
- Added structured audit logging for sensitive operations (AuditLog model + service)
- Added Architecture Decision Record (ADR) directory at `docs/adr/`
- Added `DEVELOPMENT_MODE` config field for environment-gated security controls
- Added rate limiting on `/api/health`, `/api/settings`, `/api/clusters/{id}`, `/api/strategies`
- Added input validation: preferences schema, feedback comment limit, strategy file size cap, repo name format, sort column validator
- Added shared `backend/app/utils/crypto.py` with `derive_fernet()` and `decrypt_with_migration()`

### Changed
- Passthrough template now provides per-dimension scale anchors and calibration guidance for external LLMs
- Hardened cookie security: SameSite=Lax, environment-gated Secure flag, /api path scope, 14-day session lifetime
- Restricted CORS to explicit method/header allowlists
- Sanitized error messages across all routers (no exception detail leakage)
- Validated X-Forwarded-For IPs via `ipaddress` module
- Hardened SSE `format_sse()` to handle serialization failures gracefully
- Migrated Fernet encryption from SHA256 to PBKDF2 with transparent legacy fallback
- Extended API key validation to length check (>=40 chars)
- Pinned all Python and frontend dependencies to exact versions (ADR-003)

### Fixed
- Clamped external passthrough scores to [1.0, 10.0] before hybrid blending
- Excluded `hybrid_passthrough` from z-score historical stats to prevent cross-mode contamination
- Normalized heuristic scorer clamping to consistent `max(1.0, min(10.0, score))` pattern
- Fixed contradictory scoring instructions in passthrough template ("Score both" vs "Score optimized only")
- Added `wss://` to CSP for secure WebSocket connections
- Enabled HSTS header in nginx (conditional on TLS)
- Tightened data directory permissions to 0700
- Scoped `init.sh` process discovery to current user
- Genericized nginx 50x error page (no branding/version leakage)
- Fixed logout cookie deletion to match path-scoped session cookie

## v0.3.2 — 2026-03-25

### Added
- Added `TierBadge` component with CLI/API sub-tier labels for internal tier (shows "CLI" or "API" instead of generic "INTERNAL")
- Added `models_by_phase` JSON column to Optimization model — persists per-phase model IDs for both internal and sampling pipelines
- Added per-phase model ID capture in SSE events (`model` field on phase-complete status events)
- Added `tierColor` and `tierColorRgb` getters to routing store — single source of truth for tier accent colors
- Added `--tier-accent` and `--tier-accent-rgb` CSS custom properties at layout level, inherited by all components
- Added tier-adaptive Provider/Connection/Routing section in Navigator (passthrough=Routing, sampling=Connection, internal=Provider)
- Added tier-adaptive System section in Navigator (reduced rows for passthrough/sampling, full for internal)
- Added IDE Model display section in Navigator for sampling tier — shows actual model IDs per phase in real time
- Added `.data-value.neon-green` CSS utility class
- Added shared `semantic_check()`, `apply_domain_gate()`, `resolve_effective_strategy()` helpers in `pipeline_constants.py`

### Changed
- Removed advisory MCP `ModelPreferences`/`ModelHint` from sampling pipeline — IDE selects model freely; actual model captured per phase and displayed in UI
- Total tier-aware accent branding across entire UI: SYNTHESIZE button, active tab underline, strategy list highlight, activity bar indicator, brand logo SVG, pattern suggestions, feedback buttons, refinement components, command palette, topology controls, score sparkline, markdown headings, global focus rings, selection highlight, and all action buttons adapt to tier color (cyan=CLI/API, green=sampling, yellow=passthrough)
- Navigator section headings use unified `sub-heading--tier` class (replaces per-tier `sub-heading--sampling`/`sub-heading--passthrough` classes)
- StatusBar shows CLI/API sub-tier badges instead of generic "INTERNAL" + separate "cli" text; version removed (displayed in System accordion)
- API key input redesigned as inline data-row with `pref-input`/`pref-btn` classes matching dropdown density
- SamplingGuide modal updated to remove hint/advisory language
- PassthroughView interactive elements (COPY button, focus rings) now correctly use yellow instead of cyan
- MCP disconnect detection reads `mcp_session.json` before disconnecting to detect cross-process activity the backend missed
- CLAUDE.md sampling detection section updated — replaced stale "optimistic strategy" with accurate "capability trust model" description
- RoutingManager: improved logging (session invalidation, stale capability recovery, disconnect checker fallback), type hints (`sync_from_event` signature), and docstrings (`_persist`, `RoutingState`)
- DRY: `prefs.resolve_model()` calls captured once and reused in `pipeline.py` and `tools/analyze.py`
- Replaced duplicated strategy resolution logic in `pipeline.py` and `sampling_pipeline.py` with shared helpers from `pipeline_constants.py`

### Fixed
- False MCP disconnect after 5 minutes in cross-process setup — backend disconnect checker now reads session file for fresh activity before clearing sampling state
- Missing `models_by_phase` in passthrough completion paths (REST save and MCP save_result)
- Missing `models_by_phase` in analyze tool's internal provider path
- PassthroughView COPY button and focus rings were incorrectly cyan (now yellow)
- Stale Navigator tests for removed UI elements (Model Hints, Effort Hints, "// via IDE", passthrough-mode class, "SET KEY"/"REMOVE" labels, version display)
- Activity throttle preventing routing state change broadcasts during MCP SSE reconnection
- Degradation messages hardcoding fallback tier

### Removed
- Removed `ModelPreferences`, `ModelHint`, `_resolve_model_preferences()`, `_PHASE_PRESETS`, `_PREF_TO_MODEL`, `_EFFORT_PRIORITIES` from sampling pipeline (~95 lines)
- Removed `.passthrough-mode` class from SYNTHESIZE button (tier accent handles all tiers)
- Removed per-component `style:--tier-accent` bindings (6 components) — replaced by single layout-level propagation
- Removed redundant version display from StatusBar (available in System accordion)
- Removed deprecated `preparePassthrough()` API function and `PassthroughPrepareResult` type from frontend client

## v0.3.1 — 2026-03-24

### Added
- Added unified `ContextEnrichmentService` replacing 5 scattered context resolution call sites with a single `enrich()` entry point
- Added `HeuristicAnalyzer` for zero-LLM passthrough classification (task_type, domain, weaknesses, strengths, strategy recommendation)
- Augmented `RepoIndexService` with type-aware structured file outlines and `query_curated_context()` for token-conscious codebase retrieval
- Added analysis summary, codebase context from pre-built index, applied meta-patterns, and task-specific adaptation state to passthrough tier
- Added config settings: `INDEX_OUTLINE_MAX_CHARS`, `INDEX_CURATED_MAX_CHARS`, `INDEX_CURATED_MIN_SIMILARITY`, `INDEX_CURATED_MAX_PER_DIR`, `INDEX_DOMAIN_BOOST`
- Enhanced `RootsScanner` with subdirectory discovery: `discover_project_dirs()` detects immediate subdirectories containing manifest files (`package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `go.mod`) and skips ignored dirs (`node_modules`, `.venv`, `__pycache__`, etc.)
- Expanded `GUIDANCE_FILES` list to include `GEMINI.md`, `.clinerules`, and `CONVENTIONS.md`
- Updated `RootsScanner.scan()` to scan root + manifest-detected subdirectories and deduplicate identical content by SHA256 hash (root copy wins)
- Added frontend tier resolver (`routing.svelte.ts`) — unified derived state mirroring the backend's 5-tier priority chain (force_passthrough > force_sampling > internal > auto_sampling > passthrough)
- Added tier-adaptive Navigator settings panel — Models, Effort, and pipeline feature toggles (Explore/Scoring/Adaptation) are hidden in passthrough mode since they are irrelevant without an LLM
- Added passthrough workflow guide modal — interactive stepper explaining the 6-step manual passthrough protocol, feature comparison matrix across all three execution tiers, and "don't show on toggle" preference. Triggered on passthrough toggle enable and via help button in PassthroughView header.
- Exposed `refine_rate_limit` and `database_engine` in `GET /api/settings` endpoint
- Added Version row to System section (sourced from health polling via `forgeStore.version`)
- Added Database, Refine rate rows to System section
- Added Score health (mean, stddev with clustering warning) and Phase durations to System section from health polling
- Added per-phase effort preferences: `pipeline.analyzer_effort`, `pipeline.scorer_effort` (default: `low`)
- Expanded `pipeline.optimizer_effort` to accept `low` and `medium` (was `high`/`max` only)
- Threaded `cache_ttl` parameter through full provider chain (base → API → CLI → pipeline → refinement)
- Added EFFORT section in settings panel with per-phase effort controls (low/medium/high/max)
- Included effort level in trace logger output for each phase
- Added streaming support for optimize/refine phases via `messages.stream()` + `get_final_message()` — prevents HTTP timeouts on long Opus outputs up to 128K tokens
- Added `complete_parsed_streaming()` to LLM provider interface with fallback default in base class
- Added `streaming` parameter to `call_provider_with_retry()` dispatcher
- Added `optimizer_effort` user preference (`"high"` | `"max"`) with validation and sanitization in `PreferencesService`
- Added 7 new MCP tools completing the autonomous LLM workflow: `synthesis_health`, `synthesis_strategies`, `synthesis_history`, `synthesis_get_optimization`, `synthesis_match`, `synthesis_feedback`, `synthesis_refine`
- Extracted MCP tool handlers into `backend/app/tools/` package (11 modules) — `mcp_server.py` is now a thin ~420-line registration layer
- Added `tools/_shared.py` for module-level state management (routing, taxonomy engine) with setter/getter pattern
- Added per-phase JSONL trace logging to the MCP sampling pipeline (`provider: "mcp_sampling"`, token counts omitted as MCP sampling does not expose them)
- Added optional `domain` and `intent_label` parameters to `synthesis_save_result` MCP tool (backward-compatible, defaults to `"general"`)
- Extracted shared `auto_inject_patterns()` into `services/pattern_injection.py` and `compute_optimize_max_tokens()` into `pipeline_constants.py` — eliminates duplication between internal and sampling pipelines
- Added optional `domain` and `intent_label` fields to REST `PassthroughSaveRequest` for parity with MCP `synthesis_save_result`
- Added adaptation state injection to all passthrough prepare paths (REST inline, REST dedicated, MCP `synthesis_prepare_optimization`)

### Changed
- Shared `EmbeddingService` singleton across taxonomy engine and `ContextEnrichmentService` in both FastAPI and MCP lifespans (was creating duplicate instances)
- Changed `EnrichedContext.context_sources` to use `MappingProxyType` for runtime immutability (callers convert to `dict()` at DB boundary)
- Changed `HeuristicAnalyzer._score_category()` to use word-boundary regex matching instead of substring search (prevents false positives like "class" matching "classification")
- Removed unused `prompt_lower` parameter from `_classify_domain()` helper
- Updated `ContextEnrichmentService.enrich()` to respect `preferences_snapshot["enable_adaptation"]` to skip adaptation state resolution when disabled
- Improved error logging when `ContextEnrichmentService` init fails — now explicitly warns that passthrough and pattern resolution will be unavailable
- Persisted `task_type`, `domain`, `intent_label`, and `context_sources` from heuristic analysis for passthrough optimizations (previously hardcoded "general")
- Added `EnrichedContext` accessor properties (`task_type`, `domain_value`, `intent_label`, `analysis_summary`, `context_sources_dict`) eliminating 20+ repeated null-guard expressions across call sites
- Added content capping to `ContextEnrichmentService`: codebase context capped at `MAX_CODEBASE_CONTEXT_CHARS` and wrapped in `<untrusted-context>`, adaptation state capped at `MAX_ADAPTATION_CHARS`
- Corrected `HeuristicAnalyzer` keyword signals to match spec: added 8 missing keywords (`database`, `create`, `data`, `pipeline`, `query`, `setup`, `auth`), corrected 5 weights (`write` 0.5→0.6, `design` 0.5→0.7, `API` 0.7→0.8, `index` 0.5→0.6, `deploy` 0.7→0.8)
- Pre-compiled word-boundary regex patterns at module load time (was recompiling ~100+ patterns per analysis call)
- Updated `_detect_weaknesses` and `_detect_strengths` to receive pre-computed `has_constraints`/`has_outcome`/`has_audience` flags instead of re-scanning keyword sets
- Used `is_question` structural signal to influence analysis classification (boosts analysis type when question form detected)
- Updated intent labels for non-general domains to include trailing "task" suffix per spec (e.g. "implement backend coding task")
- Changed intent label verb fallback to produce `"{task_type} optimization"` per spec (was `"optimize {task_type} task"`)
- Added "target audience unclear" weakness check for writing/creative prompts (spec compliance)
- Raised underspecification threshold from 15 to 50 words per spec
- Added optional `repo_full_name` field to REST `OptimizeRequest` — enables curated codebase context for web UI passthrough optimizations
- Removed unused `_prompts_dir` and `_data_dir` instance attributes from `ContextEnrichmentService`
- Updated `WorkspaceIntelligence._detect_stack()` to use `discover_project_dirs()` for monorepo subdirectory scanning
- Expanded `passthrough.md` template with `{{analysis_summary}}`, `{{codebase_context}}`, and `{{applied_patterns}}` sections
- Migrated all optimize/prepare/refine call sites to use unified `ContextEnrichmentService.enrich()` instead of inline context resolution
- Suppressed refinement timeline for passthrough results — refinement requires a local provider and would 503
- Hid stale phase durations from Navigator System section in passthrough mode
- Changed hardcoded "hybrid" scoring label to dynamic — shows "heuristic" in passthrough mode
- Hid internal provider jargon (`web_passthrough`) in Inspector for passthrough results
- Added "(passthrough)" suffix to heuristic scoring label in Inspector for passthrough results
- Increased passthrough scoring rubric cap from 2000 to 4000 chars (all 5 dimension definitions now included)
- Replaced vague JSON output instruction in passthrough template with structured schema example
- Added rate limiting to `POST /api/optimize/passthrough/save` (was unprotected)
- Added `max_context_tokens` validation in prepare handler (rejects non-positive values)
- Added `workspace_path` directory validation (skips non-existent paths instead of scanning arbitrary locations)
- Added `codebase_context`, `domain`, `intent_label` fields to `SaveResultInput` schema (matches tool wrapper)
- Removed unused `detect_divergence()` from `HeuristicScorer` (dead code; `blend_scores` has its own inline check)
- Standardized heuristic scorer rounding to 2 decimal places across all dimensions
- Added `DimensionScores.from_dict()` / `.to_dict()` helpers — eliminated 11 repeated dict↔model conversion patterns across passthrough code paths
- Used `DimensionScores.compute_deltas()` and `.overall` instead of manual computation in passthrough save handlers
- Extracted strategy normalization into `StrategyLoader.normalize_strategy()` — removed duplicated fuzzy matching logic from `save_result.py` and `optimize.py`
- Changed pipeline analyze and score phases to use `effort="low"` (was `"medium"`), reducing latency 30-40%
- Reduced analyze and score max_tokens from 16384 to 4096 (matching actual output size)
- Extended scoring system prompt cache TTL from 5min to 1h (fewer cache writes)
- Expanded system prompt (`agent-guidance.md`) to 5000+ tokens for cache activation across all providers
- Raised optimize/refine `max_tokens` cap from 65,536 to 131,072 (safe with streaming)
- Refactored `anthropic_api.py` — extracted `_build_kwargs()`, `_track_usage()`, `_raise_provider_error()` helpers, eliminating ~70 lines of duplicated error handling
- Rewrote all 11 MCP tool descriptions for LLM-first consumption with chaining hints (When → Returns → Chain)
- Removed prompt echo from `AnalyzeOutput.optimization_ready` to eliminate token waste on large prompts
- Extracted shared `build_scores_dict()` helper into `tools/_shared.py` (eliminates duplication in get_optimization + refine handlers)
- Moved inline imports to module level in health, history, and optimize handlers for consistency
- Imported `VALID_SORT_COLUMNS` from `OptimizationService` in history handler (single source of truth, no divergence risk)
- Renamed `_VALID_SORT_COLUMNS` to `VALID_SORT_COLUMNS` in optimization_service.py (public API for cross-module use)
- Replaced `hasattr` checks with direct attribute access on ORM columns in get_optimization and match handlers

### Removed
- Removed `resolve_workspace_guidance()` from `tools/_shared.py` (replaced by `ContextEnrichmentService`)

### Fixed
- Wrapped `_resolve_workspace_guidance` call to `WorkspaceIntelligence.analyze()` in try/except — unguarded call could crash the entire enrichment request on unexpected errors
- Fixed `test_prune_weekly_best_retention` — used hour offsets instead of day offsets so 3 test snapshots always land in the same ISO week regardless of test execution date
- Removed double-correction (bias + z-score) from passthrough hybrid scoring that systematically deflated passthrough scores vs internal pipeline
- Fixed asymmetric delta computation in MCP `save_result` — original scores now use the same blending pipeline as optimized scores
- Fixed heuristic-only passthrough path running through `blend_scores()` z-score normalization (designed for LLM scores only)
- Guarded `_recover_state()` in routing against corrupt `mcp_session.json` (non-dict JSON crashed MCP server startup)
- Fixed `available_tiers` truthiness check inconsistency with `resolve_route()` identity check
- Fixed SSE error/end handlers not recognizing passthrough mode — UI no longer gets stuck in "analyzing" on connection drop
- Added passthrough session persistence to localStorage — page refresh no longer loses assembled prompt and trace state
- Wired `check_degenerate()` into `FeedbackService.create_feedback()` — degenerate feedback (>90% same rating over 10+ feedbacks) now skips affinity updates to freeze saturated counters
- Added analyzer strategy validation against disk in both `pipeline.py` and `sampling_pipeline.py` — hallucinated strategy names now fall back to validated fallback instead of silently polluting the DB
- Added orphaned strategy affinity cleanup at startup — removes `StrategyAffinity` rows for strategies no longer on disk
- Made confidence gate fallback resilient — `resolve_fallback_strategy()` validates "auto" exists on disk, falls back to first available strategy if not. No more hardcoded `"auto"` assumption
- Added programmatic adaptation enforcement — strategies with approval_rate < 0.3 and ≥5 feedbacks are filtered from the analyzer's available list and overridden post-selection. Adaptation is no longer advisory-only
- Wired file watcher to sanitize preferences on strategy deletion — when a strategy file is deleted, the persisted default preference is immediately reset if it references the deleted strategy
- Changed event bus overflow strategy — full subscriber queues now drop oldest event instead of killing the subscriber connection, preventing silent SSE disconnections
- Added sequence numbers and replay buffer (200 events) to event bus — enables `Last-Event-ID` reconnection replay in SSE endpoint
- Added SSE reconnection reconciliation — frontend refetches health, strategies, and cluster tree after EventSource reconnects to cover any missed events
- Added `preferences_changed` event — `PATCH /api/preferences` now publishes to event bus; frontend preferences store updates reactively via SSE
- Added visibility-change fallback for strategy dropdown — re-fetches strategy list when browser tab becomes visible, defense-in-depth against missed SSE events
- Added cluster detail refresh on taxonomy change — `invalidateClusters()` now also refreshes the Inspector detail view when a cluster is selected
- Added toast notification on failed session restore — users now see "Previous session could not be restored" instead of silent empty state
- Changed taxonomy engine to use lazy provider resolution — `_provider` is now a property that resolves via callable, ensuring hot-reloaded providers (API key change) are picked up automatically
- Added 5-minute TTL to workspace intelligence cache — workspace profiles now expire and re-scan manifest files instead of caching indefinitely until restart
- Added `invalidate_all()` method to explore cache for manual full flush
- Fixed double retry on Anthropic API provider — SDK default `max_retries=2` compounded with app-level retry for up to 6 attempts; now set to `max_retries=0`
- Fixed 3 unprotected LLM call sites (`codebase_explorer`, `taxonomy/labeling`, `taxonomy/family_ops`) missing retry wrappers — transient 429/529 errors silently dropped results
- Fixed effort parameter passed to Haiku models in both API and CLI providers — Haiku doesn't support effort
- Fixed flaky `test_prune_daily_best_retention` — snapshots created near midnight UTC could cross calendar day boundaries
- Fixed MCP internal pipeline path missing `taxonomy_engine` — MCP-originated internal runs now include domain mapping and auto-pattern injection
- Fixed sampling pipeline missing auto-injection of cluster meta-patterns (only used explicit `applied_pattern_ids`, never auto-discovered)
- Fixed sampling pipeline using fixed 16384 `max_tokens` for optimize phase — now dynamically scales with prompt length (16K–65K), matching internal pipeline
- Fixed REST passthrough save using raw heuristic scores without z-score normalization — now applies `blend_scores()` for consistent scoring across all paths
- Fixed `synthesis_save_result` not persisting `domain`, `domain_raw`, or `intent_label` fields for passthrough optimizations
- Fixed `SaveResultOutput.strategy_compliance` description — documented values now match actual output ('matched'/'partial'/'unknown')
- Removed redundant re-raise pattern in feedback handler (`except ValueError: raise ValueError(str)` → let exception propagate)
- Removed unused `selectinload` import from refine handler
- Updated README.md MCP section from 4 to 11 tools with complete tool listing
- Fixed test patch targets for health and history tests after moving imports to module level
- Fixed REST passthrough save event bus notification missing `intent_label`, `domain`, `domain_raw` fields — taxonomy extraction listener now receives full metadata
- Fixed passthrough prompt assembly missing adaptation state in all three prepare paths (REST inline, REST dedicated endpoint, MCP tool)
- Fixed REST dedicated passthrough prepare ignoring `workspace_path` — now scans workspace for guidance files matching the inline passthrough path
- Fixed REST passthrough save missing `scores`, `task_type`, `strategy_used`, and `model` fields — now accepts all fields the `passthrough.md` template instructs the external LLM to return
- Fixed REST passthrough save always using heuristic-only scoring — now supports hybrid blending when external LLM scores are provided (mirrors MCP `save_result` logic)
- Fixed REST passthrough save not normalizing verbose strategy names from external LLMs (now uses same normalization as MCP `save_result`)

## v0.3.0 — 2026-03-22

### Added
- Added 3-phase pipeline orchestrator (analyze → optimize → score) with independent subagent context windows
- Added hybrid scoring engine — blended LLM scores with model-independent heuristics via `score_blender.py`
- Added Z-score normalization against historical distribution to prevent score clustering
- Added scorer A/B randomization to prevent position and verbosity bias
- Added provider error hierarchy with typed exceptions (RateLimitError, AuthError, BadRequestError, OverloadedError)
- Added shared retry utility (`call_provider_with_retry`) with smart retryable/non-retryable classification
- Added token usage tracking with prompt cache hit/miss stats
- Added 3-tier provider layer (Claude CLI, Anthropic API, MCP passthrough) with auto-detection
- Added Claude CLI provider — native `--json-schema` structured output, `--effort` flag, subprocess timeout with zombie reaping
- Added Anthropic API provider — typed SDK exception mapping, prompt cache logging
- Added prompt template system with `{{variable}}` substitution, manifest validation, and hot-reload
- Added 6 optimization strategies with YAML frontmatter (tagline, description) for adaptive discovery
- Added context resolver with per-source character caps and `<untrusted-context>` injection hardening
- Added workspace roots scanning for agent guidance files (CLAUDE.md, AGENTS.md, .cursorrules, etc.)
- Added SHA-based explore caching with TTL and LRU eviction
- Added startup template validation against `manifest.json`
- Added MCP server with 4 tools (`synthesis_optimize`, `synthesis_analyze`, `synthesis_prepare_optimization`, `synthesis_save_result`) — all return Pydantic models with `structured_output=True` and expose `outputSchema` to MCP clients
- Added GitHub OAuth integration with Fernet-encrypted token storage
- Added codebase explorer with semantic retrieval + single-shot Haiku synthesis
- Added sentence-transformers embedding service (`all-MiniLM-L6-v2`, 384-dim) with async wrappers
- Added heuristic scorer with 5-dimension analysis (clarity, specificity, structure, faithfulness, conciseness)
- Added passthrough bias correction (default 15% discount) for MCP self-rated scores
- Added optimization CRUD with sort/filter, pagination envelope, and score distribution tracking
- Added feedback CRUD with synchronous adaptation tracker update
- Added strategy affinity tracking with degenerate pattern detection
- Added conversational refinement with version history, branching/rollback, and 3 suggestions per turn
- Added API key management (GET/PATCH/DELETE) with Fernet encryption at rest
- Added health endpoint with score clustering detection, recent error counts, per-phase duration metrics, `sampling_capable`, `mcp_disconnected`, and `available_tiers` fields
- Added trace logger writing per-phase JSONL to `data/traces/` with daily rotation
- Added in-memory rate limiting (optimize 10/min, refine 10/min, feedback 30/min, default 60/min)
- Added real-time event bus — SSE stream with optimization, feedback, refinement, strategy, taxonomy, and routing events
- Added persistent user preferences (model selection, pipeline toggles, default strategy)
- Added `intent_label` (3-6 word phrase) and `domain` fields to optimization analysis — extracted by analyzer, persisted to DB, included in history and single-optimization API responses
- Added `extract_patterns.md` Haiku prompt template for meta-pattern extraction
- Added `applied_patterns` parameter to optimization pipeline — injects user-selected meta-patterns into optimizer context
- Added background pattern extraction listener on event bus (`optimization_created` → async extraction)
- Added `pipeline.force_passthrough` preference toggle — forces passthrough mode in both MCP and frontend, mutually exclusive with `force_sampling`
- Added `pipeline.force_sampling` preference toggle — forces sampling pipeline (IDE's LLM) even when a local provider is detected; gracefully falls through to local provider if sampling fails
- Added ASGI middleware on MCP server that detects sampling capability at `initialize` handshake — writes `mcp_session.json` before any tool call
- Added runtime MCP sampling capability detection via `data/mcp_session.json` — optimistic strategy prevents multi-session flicker (False never overwrites fresh True within 30-minute staleness window)
- Added evolutionary taxonomy engine (`services/taxonomy/`, 10 submodules: `engine.py`, `family_ops.py`, `matching.py`, `embedding_index.py`, `clustering.py`, `lifecycle.py`, `quality.py`, `projection.py`, `coloring.py`, `labeling.py`, `snapshot.py`, `sparkline.py`) — self-organizing hierarchical clustering with 3-path execution model: hot path (per-optimization embedding + nearest-node cosine search), warm path (periodic HDBSCAN clustering with speculative lifecycle mutations), cold path (full refit + UMAP 3D projection + OKLab coloring + Haiku labeling)
- Added quality metrics system (Q_system) with 5 dimensions: coherence, separation, coverage, DBCV, stability — adaptive threshold weights scale by active node count; DBCV linear ramp over 20 samples
- Added 4 lifecycle operations: emerge (new cluster detection), merge (cosine ≥0.78 similarity), split (coherence < 0.5), retire (idle nodes) — non-regressive gate ensures Q_system never degrades
- Added process-wide taxonomy engine singleton (`get_engine()`/`set_engine()`) with thread-safe double-checked locking
- Added `TaxonomySnapshot` model — audit trail for every warm/cold path with operation log + full tree state (JSON) and configurable retention
- Added UMAP 3D projection with Procrustes alignment for incremental updates and PCA fallback for < 5 points
- Added OKLab color generation from UMAP position — perceptually uniform on dark backgrounds with enforced minimum sibling distance
- Added LTTB downsampling for Q_system sparklines (preserves shape in ≤30 points) with OLS trend normalization
- Added Haiku-based 2–4 word cluster label generation from member text samples
- Added unified `PromptCluster` model — single entity with lifecycle states (candidate → active → mature → template → archived), self-join `parent_id`, L2-normalized centroid embedding, per-node metrics, intent/domain/task_type, usage counts, avg_score, preferred_strategy
- Added `MetaPattern` model — reusable technique extracted from cluster members with `cluster_id` FK, enriched on duplicate (cosine ≥0.82 pattern merge)
- Added `OptimizationPattern` join model linking `Optimization` → `PromptCluster` with similarity score and relationship type
- Added in-memory numpy `EmbeddingIndex` for O(1) cosine search across cluster centroids
- Added `PromptLifecycleService` — auto-curation (stale archival, quality pruning), state promotion (active → mature → template), temporal usage decay (0.9× after 30d inactivity), strategy affinity tracking, orphan backfill
- Added unified `/api/clusters/*` router — paginated list with state/domain filter, detail with children/breadcrumb/optimizations, paste-time similarity match, tree for 3D viz, stats with Q metrics + sparkline, proven templates, recluster trigger, rename/state override — with 301 legacy redirects for `/api/patterns/*` and `/api/taxonomy/*`
- Added `ClusterNavigator` with state filter tabs, domain filter, and Proven Templates section
- Added state-based chromatic encoding in `SemanticTopology` (opacity, size multiplier, color override per lifecycle state)
- Added template spawning — mature clusters promote to templates, "Use" button pre-fills editor
- Added auto-injection of cluster meta-patterns into optimizer pipeline (pre-phase context injection via `EmbeddingIndex` search)
- Added auto-suggestion banner on paste — detects similar clusters with 1-click apply/skip (50-char delta threshold, 300ms debounce, 10s auto-dismiss)
- Added Three.js 3D topology visualization (`SemanticTopology.svelte`) with LOD tiers (far/mid/near persistence thresholds), raycasting click-to-focus, billboard labels, and force-directed collision resolution
- Added `TopologyControls` overlay — Q_system badge, LOD tier indicator, Ctrl+F search, node counts
- Added canvas accessibility — `aria-label`, `tabindex`, `role="tooltip"` on hover, `role="alert" aria-live="polite"` on error
- Added `taxonomyColor()` and `qHealthColor()` to `colors.ts` — resolves hex, domain names, or null to fallback color
- Added cluster detail in Inspector — meta-patterns, linked optimizations, domain badge, usage stats, rename
- Added StatusBar breadcrumb segment showing `[domain] > intent_label` for the active optimization with domain color coding
- Added intent_label + domain badge display in History Navigator rows (falls back to truncated `raw_prompt` for pre-knowledge-graph optimizations)
- Added intent_label as editor tab title for result and diff tabs (falls back to existing word-based derivation from `raw_prompt`)
- Added live cluster link — `pattern_updated` SSE auto-refreshes current result to pick up async cluster assignment
- Added composite database index on `optimization_patterns(optimization_id, relationship)` for cluster lookup performance
- Added `intent_label` and `domain` to SSE `optimization_complete` event data for immediate breadcrumb display
- Added centralized intelligent routing service (`routing.py`) with pure 5-tier priority chain: force_passthrough > force_sampling > internal provider > auto sampling > passthrough fallback
- Added `routing` SSE event as first event in every optimize stream (tier, provider, reason, degraded_from)
- Added `routing_state_changed` ambient SSE event for real-time tier availability changes
- Added `RoutingManager` with in-memory live state, disconnect detection, MCP session file write-through for restart recovery, and SSE event broadcasting
- Added structured output via tool calling in MCP sampling pipeline — sends Pydantic-derived `Tool` schemas via `tools` + `tool_choice` on `create_message()`, falls back to text parsing when client doesn't support tools
- Added model preferences per sampling phase (analyze=Sonnet, optimize=Opus, score=Sonnet, suggest=Haiku) via `ModelPreferences` + `ModelHint`
- Added sampling fallback to `synthesis_analyze` — no longer requires a local LLM provider
- Added full feature parity in sampling pipeline: explore (via `SamplingLLMAdapter`), applied patterns, adaptation state, suggest phase, intent drift detection, z-score normalization
- Added `applied_pattern_ids` parameter to `synthesis_optimize` MCP tool — injects selected meta-patterns into optimizer context (mirrors REST API)
- Added PASSTHROUGH badge in Navigator Defaults section (amber warning color) when `force_passthrough` is active
- Added SvelteKit 2 frontend with VS Code workbench layout and industrial cyberpunk design system
- Added prompt editor with strategy picker, forge button, and SSE progress streaming
- Added result viewer with copy, diff toggle, and feedback (thumbs up/down)
- Added 5-dimension score card with deltas in Inspector panel
- Added side-by-side diff view with dimmed original
- Added command palette (Ctrl+K) with 6 actions
- Added refinement timeline with expandable turn cards, suggestion chips, and score sparkline
- Added branch switcher for refinement rollback navigation
- Added live history navigator with API data and auto-refresh
- Added GitHub navigator with repo browser and link management
- Added session persistence via localStorage — page refresh restores last optimization from DB
- Added toast notification system with chromatic action encoding
- Added landing page with hero, features grid, testimonials, CTA, and 15 content subpages
- Added CSS scroll-driven animations (`animation-timeline: view()`) with progressive enhancement fallback
- Added View Transitions API for cross-page navigation morphing
- Added GitHub Pages deployment via Actions artifacts (zero-footprint, no `gh-pages` branch)
- Added Docker single-container deployment (backend + frontend + MCP + nginx)
- Added init.sh service manager with PID tracking, process group kill, preflight checks, and log rotation
- Added version sync system (`version.json` → `scripts/sync-version.sh` propagates everywhere)
- Added `umap-learn` and `scipy` backend dependencies

### Changed
- Changed `domain` from `Literal` type to free-text `str` — analyzer writes unconstrained domain, taxonomy engine maps to canonical node
- Changed pattern matching to hierarchical cascade: nearest active node → walk parent chain → breadcrumb path
- Changed usage count propagation to walk up the taxonomy tree on each optimization
- Changed `model_used` in sampling pipeline from hardcoded `"ide_llm"` to actual model ID captured from `result.model` on each sampling response
- Changed `synthesis_optimize` MCP tool to 5 execution paths: force_passthrough → force_sampling → provider → sampling fallback → passthrough fallback
- Enforced `force_sampling` and `force_passthrough` as mutually exclusive — server-side (422) and client-side (radio toggle behavior)
- Disabled Force IDE sampling toggle when sampling is unavailable or passthrough is active
- Disabled Force passthrough toggle when sampling is available or `force_sampling` is active
- Changed `POST /api/optimize` to handle passthrough inline via SSE (no more 503 dead end when no provider)
- Changed frontend to be purely reactive for routing — backend owns all routing decisions via SSE events
- Changed health endpoint to read live routing state from `RoutingManager` instead of `mcp_session.json` file reads
- Changed provider set/delete endpoints to update `RoutingManager` state
- Changed refinement endpoint to use routing service (rejects passthrough tier with 503)
- Changed MCP server to use `RoutingManager` for all routing decisions
- Changed frontend health polling to fixed 60s interval (display only, no routing decisions)
- Enriched history API and single-optimization API with `intent_label`, `domain`, and `cluster_id` fields (batch lookup via IN query, not N+1)
- Added `intent_label` and `domain` to `_VALID_SORT_COLUMNS` in optimization service
- Extracted `sampling_pipeline.py` service module from `mcp_server.py` for maintainability

### Fixed
- Fixed process-wide taxonomy engine singleton with thread-safe double-checked locking (was creating multiple engine instances)
- Fixed task lifecycle — extraction tasks tracked in `set[Task]` with `add_done_callback` cleanup and 5s shutdown timeout
- Fixed usage propagation timing — split `_resolve_applied_patterns()` into read-only resolution + post-commit increment (avoids expired session)
- Fixed null label guard in `buildSceneData` — runtime `null` labels coerced to empty string
- Fixed `SemanticTopology` tooltip using non-existent CSS tokens (`--color-surface`, `--color-contour`)
- Fixed circular import between `forge.svelte.ts` and `clusters.svelte.ts` — `spawnTemplate()` returns data instead of writing to other stores
- Fixed dead `context_injected` SSE handler in `+page.svelte` — moved to `forge.svelte.ts` where optimization stream events are processed
- Fixed `pattern_updated` SSE event type missing from `connectEventStream` event types array — handler was dead code
- Fixed Inspector linked optimizations using `id` instead of `trace_id` for API fetch — would always 404
- Fixed `PipelineResult` schema missing `intent_label` and `domain` — SSE `optimization_complete` events now include analyzer output
- Fixed Inspector linked optimization display to use `intent_label` with fallback to truncated `raw_prompt`
- Fixed sampling pipeline missing confidence gate and semantic check — low-confidence strategy selections were applied without the safety override to "auto"
- Fixed sampling pipeline model hint presets using short names (`claude-sonnet`) instead of full model IDs from settings
- Fixed sampling pipeline `run_sampling_pipeline()` not returning `trace_id` in result dict
- Fixed sampling pipeline `run_sampling_analyze()` computing `heur_scores` twice
- Fixed internal pipeline path in `synthesis_optimize` not including `trace_id` in `OptimizeOutput`
- Fixed Docker healthcheck to validate `/api/health` (was hitting nginx root, always 200)
- Fixed Docker to add security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy)
- Fixed Docker `text/event-stream` added to nginx gzip types
- Fixed Docker `.dockerignore` to correctly include prompt templates via `!prompts/**/*.md`
- Fixed Docker Alembic migration errors to fail hard instead of being silently ignored
- Fixed Docker entrypoint cleanup to propagate actual exit code
- Fixed CLI provider to remove invalid `--max-tokens` flag, uses native `--json-schema` instead
- Fixed pipeline scorer to use XML delimiters (`<prompt-a>`/`<prompt-b>`) preventing boundary corruption
- Fixed pipeline Phase 4 event keys to use consistent stage/state format
- Fixed pipeline refinement score events to only emit when scoring is enabled
- Fixed pipeline dynamic `max_tokens` to cap at 65536 to prevent timeout
- Fixed landing page route structure — landing at `/`, app at `/app` (fixes GitHub Pages routing)

### Removed
- Removed `auto_passthrough` preference toggle and frontend auto-passthrough logic (backend owns degradation)
- Removed `noProvider` state from forge store (replaced by routing SSE events)
- Removed frontend MCP disconnect/reconnect handlers (backend owns via SSE)
