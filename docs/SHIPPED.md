# Project Synthesis — Shipped Features Archive

Historical record of completed work, extracted from `docs/ROADMAP.md` to keep the active roadmap focused on planned/in-flight items. Entries are ordered newest-first by release tag.

For active work, see [`ROADMAP.md`](ROADMAP.md). For per-change detail with file/line references, see [`CHANGELOG.md`](CHANGELOG.md). Architectural decisions are in [`adr/`](adr/).

---

### v0.4.14 — SQLite migration finalization (2026-05-04)

Closes the v0.4.13 short-lived-write deferred sites. After v0.4.14, every short-lived write path inside the foreground request lifecycle (passthrough pending insert, sampling-pipeline persist, audit logs, OAuth flows, status updates) routes through the WriteQueue. Long-handler sites (refine, save_result, optimize internal-pipeline) and cold-path full-refit + `_bg_index`/`build_index` remain on the legacy path; all are tracked for v0.4.15 architectural cycles.

**Migration scope:** ~395 LOC across 5 cycles, ~24 new tests.

**New surface:**
- `WriteQueue.submit_batch()` helper.
- `SubmitBatchError` + `SubmitBatchCommitError` exceptions.

**Migrated sites:** `tools/optimize.py:125`, `services/sampling_pipeline.py:846`, 6 `github_auth.py` routers, 3 audit-log writers (via existing `log_event(write_queue=)` kwarg), `_update_synthesis_status` background-task helper.

**Acceptance:** OPERATE regression bar (probe + 30 seeds + 100 feedbacks + 5 optimize-pt + 3 sampling + 3 device-poll + 5 strategy_updated + 5 api_key flows) passes with 0 locks + 0 audit warns + p95 ≤ 1s + 0 queue failures.

**Time-gated follow-ups (v0.4.14.x patches):**
- Audit-hook RAISE-in-production switch — 7+ days post-v0.4.14 ship with zero locks/warns.
- `WriterLockedAsyncSession` removal — gated on RAISE switch + 7 days zero flush events.

**Deferred to v0.4.15 architectural cycles:**
- `tools/refine.py:50, :156` — handler wraps RefinementService LLM call inside session.
- `tools/save_result.py:85` — handler wraps heuristic scoring + analyzer A4 LLM fallback.
- `tools/optimize.py:198` — handler wraps PipelineOrchestrator 4-LLM SSE loop.
- Cold-path commit chunking with per-phase Q-gates.
- `_bg_index` / `RepoIndexService.build_index()` per-file write batching.

### v0.4.13 — 2026-05-04

**SQLite writer-slot contention — architectural fix (P0).** The hardest cycle of the v0.4.x line. Closes the cycle-19→22 v2 + probe v22-v29 audit chain (`docs/audits/probe-v22-v29-2026-04-29.md`) where 11 of 26 historical probe runs silently lost their optimization rows under realistic concurrent-writer load. The v0.4.12 hardening (verify-after-persist gate, per-prompt streaming, early-abort, warm-path Groundhog Day fix) made failures loud and structured but did NOT fix the root cause — confirmed when v27 ran with MCP server stopped (only backend) and still went catastrophic, proving contention was purely within-backend, not cross-process.

**Goal:** eliminate SQLite WAL writer-slot contention architecturally — replace the per-call-site retry/mutex stack with a single ordered writer.

**Architecture:**

- **Two engines** — read pool (production-config, all reads) + writer-only pool (`pool_size=1, max_overflow=0`, owned exclusively by the queue worker). Read concurrency under WAL is preserved; writer is now a single connection in monotonic order.
- **Single async worker** — `WriteQueue` (`backend/app/services/write_queue.py`) drains an `asyncio.Queue` of work functions; each work function runs against a queue-owned `AsyncSession` on the writer engine.
- **`submit(work_fn, *, timeout=300, operation_label)` API** — the canonical entry point. Returns the work function's return value. Caller cancellation does NOT cancel in-flight work (shielded `__aexit__`); per-task timeout DOES (default 300s).
- **Audit hook** — `install_read_engine_audit_hook()` fires on every read-engine `flush` containing pending writes outside the allow-list flag set (`migration_mode` for lifespan ALTER TABLE migrations + `cold_path_mode` for taxonomy cold-path full-refit). Captures the offending stack frame; structured WARNING in dev/prod, RAISE in CI via `WRITE_QUEUE_AUDIT_HOOK_RAISE`. Catches drift writes at the source instead of forcing forensic reconstruction from "database is locked" symptoms.
- **Reentrancy guard** — hard-fails via `WriteQueueReentrancyError` if `submit()` is invoked from within the worker task itself (would deadlock the queue forever).
- **Rolling p95/p99 reservoir** — submit-to-completion latency exposed on `/api/health` queue block alongside `depth`, `in_flight`, `total_submitted` / `total_completed` / `total_failed` / `total_timeout` / `total_overload`, `max_observed_depth`, `worker_alive`.

**Migration:** ~80 callsites across 11 cycles, ~11000 LOC. Hot-path: `bulk_persist`, `batch_taxonomy_assign`, `pipeline_phases.persist_and_propagate`, sampling persistence path. Warm-path: 12 phase commits in the taxonomy engine, hot-path engine 3 sites, snapshot writer, global pattern lifecycle (promote/validate/retire). Probe service: 17 sites + read-only `self.db`. Service layer: `feedback_service`, `optimization_service`, `audit_logger`, startup/recurring `gc`, `orphan_recovery`. REST routers: `optimize` / `domains` / `templates` / `github_repos` / `projects` / `clusters` / `feedback` / `history`. MCP tools: 3 of 7 migrated (rest deferred to v0.4.14). Telemetry: `task_type_telemetry` cycle 9.6 fire-and-forget submit.

**Acceptance:** cycle 10 regression bar (probe + 30 seeds + 100 feedbacks **fully concurrent**) — **122 locks → 0 locks**, **54 audit warns → 0 warns**. Backend `pytest` 3457 passing / 1 skipped / 0 failed. Validation evidence: `docs/v0.4.13-validation/` (criterion-1 backend log, db-lock-traces, health snapshots, probe-runs summary).

**Acknowledged exception — cold path:** taxonomy cold-path full-refit retained on `WriterLockedAsyncSession` + `cold_path_mode` audit-hook bypass. Refit's transaction span (multi-second commits across thousands of cluster rows) does not fit the queue's per-task timeout model. v0.4.14 chunks each refit phase into smaller `submit()` calls with `await asyncio.sleep(0)` between.

**TDD discipline:** 5-phase RED → GREEN → REFACTOR → INTEGRATE → OPERATE per cycle, gated by independent 2-stage code review (spec verification + code-quality). Spec went through 6 review rounds before APPROVED; plan went through 3 before APPROVED. The combined review surface caught 4 polymorphic-signature regressions, the lifespan teardown ordering bug, the test fixture PRAGMA divergence, and the extraction-listener `worker_alive` defensive check.

**Subsumes:** ROADMAP entry "SQLite writer-slot contention — architectural fix (v0.4.13 P0)" — shipped.

**Spec:** `docs/specs/sqlite-writer-queue-2026-05-02.md` (v6 APPROVED). **Plan:** `docs/superpowers/plans/2026-05-02-sqlite-writer-queue.md` (v3 APPROVED). **Branch:** `release/v0.4.13` (76 commits, all pushed). **Stats:** 76 commits, 11 TDD cycles, ~11000 LOC. **Topic Probe Tier 2** (originally targeted for v0.4.13) deferred to v0.4.13.x or v0.4.14 to keep the release scoped on the architectural fix that all subsequent tiers depend on.

### v0.4.12 — 2026-05-02

**Topic Probe Tier 1 + post-Tier-1 architectural hardening.** Agentic targeted exploration of a user-specified topic against the linked GitHub codebase. Productizes the manual cycle-15→22 workflow that emerged the `embeddings` sub-domain and `data` / `frontend` top-level domains organically. Topic Probe is a peer of seed agents — same execution primitive (`batch_pipeline`), different generation strategy (LLM-agentic-from-topic-and-codebase vs pre-authored agent template).

- **Topic Probe Tier 1** — `POST /api/probes` (SSE) / `GET /api/probes` / `GET /api/probes/{id}` REST + `synthesis_probe` MCP tool (15th tool) + `prompts/probe-agent.md` (hot-reloaded, 8 template vars) + `ProbeRun` SQLAlchemy model + idempotent migration (`ec86c86ba298`). 5-phase orchestrator: `grounding → generating → running → observability → reporting`. 7 new `probe_*` taxonomy events + `current_probe_id` ContextVar for cross-event correlation. `scripts/probe.py` CLI shim. 8 TDD cycles (RED → GREEN → REFACTOR → review per cycle), 38 ACs / 39 tests across 8 test files. Spec: `docs/specs/topic-probe-2026-04-29.md` (gitignored). All 4 Topic Probe tiers ship within the 0.4.x line: T1=v0.4.12 (this), T2=v0.4.14 (save-as-suite + replay + UI navigator — bumped from v0.4.13 after that release shipped the SQLite contention fix), T3=v0.4.15 (cross-tier composition), T4=v0.4.16 (substrate unification).
- **Probe persistence resilience** — verify-after-persist gate (no silent success: probe queries DB for canonical truth before reporting; three outcomes: full / partial / catastrophic), per-prompt streaming persistence (smaller transactions + real-time UI updates as each prompt scores), early-abort on catastrophic (cancels in-flight LLM calls within ~75s of first persist trip-wire, saves 12-20 min of Opus 4.7 audit-class calls per failed run), warm-path Groundhog Day loop fix (`_warm_path_age` advances on failure so subsequent cycles don't re-run the failed first-cycle scan).
- **Rate-limit handling end-to-end** — `ProviderRateLimitError` carries `reset_at: datetime` + `provider_name` + unified `estimated_wait_seconds` property. Plan-agnostic CLI message parsing (Pro/Team/Enterprise/MAX/Bedrock/Vertex). Graceful 429 fallback to heuristic-only `passthrough_fallback` row instead of failure. Frontend: `rate_limit_active` / `rate_limit_cleared` SSE events + `rateLimitStore` (per-second tick, multi-provider) + `RateLimitBanner.svelte` global banner + Settings panel "Rate limits" accordion + one-shot info toast on first hit.
- **`WriterLockedAsyncSession` keystone** — process-wide writer mutex eliminates SQLite "database is locked" lock storms architecturally rather than via per-call-site mutex wrapping. Subclasses `AsyncSession` to auto-acquire `db_writer_lock` on first `flush()` (gated on `self.new or self.dirty or self.deleted` so read-only sessions DON'T acquire) and release on `commit()`/`rollback()`/`close()`. Every existing commit site is automatically serialized — no refactor of the 60+ call sites. _Note: under realistic concurrent-writer load, probes may still fail catastrophic. v0.4.13 P0 carries the architectural fix (single-writer queue worker, see `docs/ROADMAP.md`)._
- **CORS-safe global error handler** — `@app.exception_handler(Exception)` echoes the request's `Origin` back when in the allowlist + sets `access-control-allow-credentials: true`. Pre-fix, 500 responses shipped without CORS headers and the browser rejected them with "ERR_FAILED" + a misleading CORS error, hiding the actual exception. Body also gains `error_type` for category-aware UI handling.
- **SSE infrastructure improvements** — periodic `event: sync` keepalive every 30s (was `: keepalive\n\n` comment, invisible to JS handlers). SSE store enters slow-poll cadence (30s) instead of permanent "Retries exhausted" state after backoff exhaustion. Visibility-change handler retries on tab return.
- **ENRICHMENT layer count UX consistency** — pre-fix the panel header showed "X/9 layers" computed from `Object.keys(context_sources).length` (which includes metadata keys), but only 4 dots rendered → cognitive mismatch. Now promotes 5 telemetry signals (RETRIEVAL, STRATEGY RANKINGS, DOMAIN SIGNALS, TASK-TYPE SCORES, CONTEXT INJECTION) to first-class dots in the layer list with per-layer `isActive` predicates. Header counts computed from `LAYER_ORDER` itself — guaranteed to match by construction.
- **Reactive bulk-delete UI** + **defensive `trace_id` query** + **probe cancellation orphan-recovery hotfix** + **optimizer timeout calibration** (300 → 600s per-LLM-call ceiling, calibrated against live audit-class p95 distribution) + **bulk_persist ID-shape gate** + **3 startup GC sweeps** + **pattern-injection provenance on probe + seed rows** (task #97).

**Stats:** 50 commits, 3290 backend tests passing / 1 skipped / 0 failed. Frontend `npm run check` 0 errors / 0 warnings, `npm run test` 1546 passed. Ruff clean. PR [#62](https://github.com/project-synthesis/ProjectSynthesis/pull/62). **Known limitation:** SQLite writer-slot contention is unresolved at the architectural level. The 8 persistence-resilience commits make failures **loud and structured** (verify-gate, early-abort, structured `probe_failed` events) but don't fix the root cause; v0.4.13 P0 carries the fix.

### v0.4.11 — 2026-04-28

**Domain proposal hardening — `fullstack` ghost-domain finding.** Closes the cycle-19→22 v2 replay forensic finding where `fullstack` was promoted from a single cluster of 3 prompts (67% consistency, coherence 0.0/skipped), then merged out leaving an empty domain node frozen by the 48h dissolution gate.

- **P0a — Domain proposal cluster-count floor** — `engine._propose_domains()` now requires ≥`DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS=2` distinct contributing clusters before promoting a top-level domain. Both proposal paths enforce the floor: per-cluster pass refactored to aggregate by `top_primary` BEFORE creating domains; pooled pass adds `len(bucket["clusters"]) >= MIN_SOURCE_CLUSTERS` check alongside the existing pooled-member gate. Rejected primaries emit `proposal_rejected_min_source_clusters` event with `{domain_label, source_cluster_count, required_min, source}` for forensic visibility. Module-level invariant assertion in `_constants.py` fails fast on `< 1` configuration drift.
- **P1 — Operator dissolve-empty endpoint** — `POST /api/domains/{id}/dissolve-empty` (10/min) lets operators force-dissolve ghost domains (`member_count == 0`, age >= `DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES=30`) without waiting for the 48h `_reevaluate_domains` gate. Idempotent (200 + `dissolved=False, reason="already_dissolved"` on re-call); 409 + `reason="not_empty"` if domain has members; 409 + `reason="too_young"` if too recent; 404 if not found; 429 on rate limit. Mirrors v0.4.8 R6 (`POST /api/domains/{id}/rebuild-sub-domains`) — operator escape hatch pattern. Emits `domain_ghost_dissolved` decision event + `taxonomy_changed` SSE on success.

**Spec:** `docs/specs/domain-proposal-hardening-2026-04-28.md` (gitignored).

### v0.4.10 — 2026-04-28

**F3.1 — Persistence wiring for analysis-weighted overall score.** v0.4.9 F3 wired analysis-aware `compute_overall(task_type)` into `score_blender.blend_scores` and the improvement_score loops, but the actual stored `overall_score` field reads from `DimensionScores.overall` (the `@property`), which always uses the default `DIMENSION_WEIGHTS`. The analysis weights were computed but never reached the database.

- **Cycle-19→22 replay confirmed the bug**: stored mean **7.155** = v3 default schema; computed-with-v4 mean **7.208**. Delta lost: **+0.053** across 19 prompts.
- **Fix**: every persistence site (DB write, SSE event payload, log line, `PipelineResult` build) where `task_type` is in scope now calls `optimized_scores.compute_overall(task_type)` instead of `optimized_scores.overall`. Touch points: `pipeline_phases.py` (5 sites), `sampling_pipeline.py` (4 sites), `batch_pipeline.py` (1 site), `pipeline.py` (2 sites). Refinement service untouched — refinement does not re-classify task_type and degrades to `None` (default-weighted) which preserves prior behavior.
- **Regression suite** `TestPersistenceWeightWiring` (3 tests): pins the divergence between `BlendedScores.overall` (analysis-weighted source of truth) and `DimensionScores.overall` (default-weighted property), verifies `compute_overall(task_type)` recovers the analysis-weighted value, and asserts via code inspection that no persistence site uses the bare `.overall` property when `task_type` is in scope. Full suite 3180 passed + 1 skipped.

### v0.4.9 — 2026-04-28

Audit-prompt scoring hardening (F1–F5) — closes all 5 audit recommendations from `docs/audits/audit-prompt-class-deep-dive-2026-04-27.md`. Per-fix TDD via RED→GREEN→REFACTOR→VALIDATION subagent dispatches, gated by independent spec verification. 24 new tests; full backend suite at 3177 passed / 1 skipped. Cycle-23 partial validation (3/5 prompts; 2 hit Opus 4.7 infrastructure timeouts).

- **F1 — Specificity heuristic credits backtick-wrapped code identifiers** — added 11th category `(r"`[a-zA-Z_][a-zA-Z0-9_./:-]*`", 0, 2.0)` to `heuristic_specificity`. Audit prompts citing real code references (`engine.py`, `_reevaluate_sub_domains`) earn structural specificity credit. Live: 3-backtick prompt scored 4.6 vs 3.0 (delta +1.6, well above the 0.5 acceptance floor).
- **F2 — `ZSCORE_MIN_STDDEV` raised 0.3 → 0.5** — bypasses z-norm on narrow-distribution task types (audits cluster at stddev~0.35 where z-norm previously floor-capped legitimately adequate raw scores). Aligns with the count >= 50 tier of the narrow-distribution gate at `routers/health.py:394`.
- **F3 — Per-task-type `DIMENSION_WEIGHTS`** — `ANALYSIS_DIMENSION_WEIGHTS` (clarity 0.25 / specificity 0.25 / structure 0.20 / faithfulness 0.20 / conciseness 0.10) selected via `get_dimension_weights(task_type)`. `DimensionScores` gains sibling `compute_overall(task_type=None)` method alongside the preserved `@property def overall` (~30 backward-compat callers). `SCORING_FORMULA_VERSION` 3 → 4. Module-level invariant assertions fail-fast on weight-sum drift.
- **F4 — `OptimizationResult.strategy_used` removed** — closes the LLM-freelance divergence window where the optimizer LLM could declare any strategy regardless of the resolved `effective_strategy`. Persisted strategy is always the orchestrator-side `effective_strategy`. Pydantic `extra="forbid"` rejects any LLM emission of the field. Refinement service ripple-fix at lines 428,460 switched `refined.strategy_used` → orchestrator-side `strategy_name`.
- **F5 — `possible_false_premise` divergence flag** — fires when an analysis-class prompt scores LLM faithfulness < 5.0 AND `technical_dense=True`. Surfaces audits whose surface symbol density may mask a wrong premise. Purely additive — does not change scores, only telemetry.

**Audit:** `docs/audits/audit-prompt-class-deep-dive-2026-04-27.md` (Resolution status footer — all F1–F5 SHIPPED). **Specs:** `docs/specs/audit-prompt-hardening-2026-04-28.md` (gitignored). **Validation:** independent code review (2 rounds, both APPROVE), cycle-23 replay (3/5 successful prompts, mean 7.35; cycle-19→22 full replay queued for follow-up).

### v0.4.8 — 2026-04-27

Sub-domain dissolution hardening — closes all 8 audit recommendations from
`docs/audits/sub-domain-regression-2026-04-27.md`. Multi-cycle TDD via
RED→GREEN→REFACTOR→VALIDATION subagent dispatches per recommendation,
gated by independent spec verification, integration validation cycles
(`cycle-12`/`cycle-13`/`cycle-14`), and a comprehensive PR-wide
systematic check. 46 new tests + 8 adapted, full backend suite at 3153
passed / 1 skipped.

- **R1 — Bayesian shrinkage on consistency** — replace point-estimate
  consistency in `_reevaluate_sub_domains()` with a Beta-Binomial
  posterior using prior strength `SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH=10`
  centered at `SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER=0.40`. Prevents
  small-N noise (one off-topic member at N=5) from triggering
  dissolution. Both `sub_domain_reevaluated` and `sub_domain_dissolved`
  events now carry `shrunk_consistency_pct` + `prior_strength` keys
  alongside the legacy `consistency_pct`. `TestSubDomainBayesianShrinkage`
  (4 tests). Spec: `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md` §R1.
- **R2 — 24h dissolution grace period** — `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS`
  bumped from 6 to 24. Both observed dissolutions in the audit incident
  fired at 6h 0m and 6h 8m post-creation — exactly at the gate.
  24 hours gives one full daily cycle of bootstrap volatility. Two
  tests in `TestSubDomainGracePeriod`.
- **R3 — Empty-snapshot guardrail** — when a sub-domain's
  `cluster_metadata.generated_qualifiers` is empty, the matcher would
  fall through to v0.4.6 exact-equality behavior (the bug class v0.4.7
  fixed). Now `_reevaluate_sub_domains()` skips dissolution and emits
  `sub_domain_reevaluation_skipped` with `reason=empty_vocab_snapshot`.
  Two tests in `TestSubDomainEmptySnapshotSkip`.
- **R4 — Per-opt matcher extracted to shared primitive** — the inline
  three-source matching cascade in `_reevaluate_sub_domains()` is now a
  pure function `match_opt_to_sub_domain_vocab() -> SubDomainMatchResult`
  in `services/taxonomy/sub_domain_readiness.py`. Engine consumes the
  primitive. Same predicate available to future tools and the rebuild
  endpoint. 7 unit tests + 1 byte-equivalence integration test.
- **R5 — Forensic dissolution telemetry** — both reevaluated and
  dissolved events now carry `matching_members` (int) and up to 3
  `sample_match_failures` entries with `cluster_id` (preserved
  unsanitized) + `domain_raw`/`intent_label` (truncated to 80 chars) +
  `reason` (from `SubDomainMatchResult`). Closes the audit's "30
  minutes of forensic work that should have been one log line" gap.
  Two new constants `SUB_DOMAIN_FAILURE_SAMPLES=3`,
  `SUB_DOMAIN_FAILURE_FIELD_TRUNCATE=80`. `TestSubDomainForensicTelemetry`
  (5 tests).
- **R6 — Operator rebuild endpoint** —
  `POST /api/domains/{domain_id}/rebuild-sub-domains` (10/min) lets
  operators force discovery on a single domain with optional
  `min_consistency` override (Pydantic `ge=0.25` floor + runtime
  defense-in-depth) and `dry_run` semantics. Idempotent. Single
  `db.begin_nested()` SAVEPOINT for partial-failure rollback. Always
  emits `sub_domain_rebuild_invoked` telemetry; publishes
  `taxonomy_changed` only when sub-domains actually create AND
  non-dry-run. 11 service tests + 6 router tests. Spec:
  `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` §R6.
- **R7 — Vocab regeneration overlap telemetry** —
  `vocab_generated_enriched` event gains `previous_groups`,
  `new_groups`, `overlap_pct` (Jaccard %). WARNING log fires when
  `overlap_pct < 50%` on a non-bootstrap regen — the audit's incident
  saw vocab swap from `{metrics, tracing, pattern-instrumentation}` to
  `{concurrency, observability, embeddings, security}` (zero overlap)
  one minute after the second sub-domain dissolved; this telemetry
  surfaces that correlation immediately. 5 tests in
  `TestVocabRegenOverlap`.
- **R8 — Threshold-collision invariant** — `_validate_threshold_invariants()`
  callable in `_constants.py` invoked at module-import time; fails fast
  if `SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW <= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`.
  Function-call form (rather than literal assert) lets tests exercise
  the invariant logic without `importlib.reload` quirks.
  `TestThresholdCollisionInvariant` (3 tests).

**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md` (Resolution
status table — all 8 SHIPPED). **Specs:** three docs under
`docs/specs/sub-domain-dissolution-hardening-{2026-04-27,r4-r6,r7-r8}.md`,
each with companion `…-plan.md`. **Validation:** comprehensive PR-wide
subagent check confirmed code, tests, CHANGELOG, spec status, and live
behavior across all R1-R8.

### v0.4.7 — 2026-04-26

Shipped: 5 root-cause MCP routing fixes (debounce + initialize suppression + recovery trust-fresh-file), TF-IDF cascade source-3 unblocked + organic feedback loop into Haiku vocab regeneration, B5/B5+ writing-about-code task-type lock + codebase trim, C1-C5 score calibration sweep, T1.x learning loops (Bayesian shrinkage, MMR few-shot, A4 confidence gate, T1.3-lite pattern usefulness counters), `heuristic_baseline_scores` deterministic baseline (C4) + idempotent migration `d3f5a8c91024`, frontend halo + observatory SSE refresh fixes.

- **MCP routing thrash root-cause sweep** — Claude Code per-tool-call SSE cycling produced visible status-flicker every 30–110s + ~994 cross-process publishes per cycle. Five fixes ship together: (1) `routing.disconnect_averted` log demote INFO → DEBUG, (2) disconnect broadcast debounced via `DISCONNECT_DEBOUNCE_SECONDS=3.0` and `loop.create_task(_deferred_disconnect_broadcast())` — re-initialize within window cancels the pending task, (3) initialize broadcast suppression via `_pre_disconnect_sampling` snapshot — halves per-tool-call publish volume when capability unchanged, (4) recovery trust-fresh-file in `_recover_state()` when both `is_capability_fresh AND not detect_disconnect` — eliminates ~60s blackhole window when FastAPI backend reloads while MCP server is alive, (5) operational uvicorn `--reload-dir app` already correctly scoped. 178 routing+sampling+migration tests pass; 3 new `TestDisconnectDebounce` regression tests + `test_capability_fresh_but_disconnected` recovery negative case. (`backend/app/services/routing.py`)
- **TF-IDF cascade source-3 unblocked + organic feedback loop** — qualifier cascade's third source reported `0` hits across every domain in live readiness telemetry for ~5 days. Two stacked bugs: (1) `_extract_domain_keywords` queried `Optimization.cluster_id == cluster.id` but domain nodes never own opts directly (opts live in child clusters), so refresh persisted `signal_keywords=[]`; (2) raw TF-IDF mean scores topped at 0.167 but the cascade admit gate requires `weight >= 0.5`. Fix shape: dual-mode query (domain nodes aggregate across descendants; regular clusters keep direct-id query) + min-max normalization. Vocab regeneration now receives `domain_signal_keywords` + `existing_vocab_groups` so Haiku absorbs latent themes the cascade is recording exclusively via source 3 — closes the organic feedback loop with no manual intervention. 3 regression tests in `test_domain_discovery.py`. (`backend/app/services/taxonomy/engine.py` + `taxonomy/labeling.py`)
- **B5/B5+ writing-about-code path** — three regressions stacked when a writing/creative prompt has technical anchors in its first sentence: B2 first-sentence rescue catches them via inline backticks → `code_aware`, LLM analyzer flips `task_type` writing→coding, codebase-context layer delivers full 80K curated retrieval, optimizer hallucinates against related-but-wrong code. Fix shape: (1) **B5+ task-type lock** in `pipeline_phases.resolve_post_analyze_state` — prefer the writing lead verb when heuristic also says writing and LLM says coding; `write` requires a prose-output cue. (2) **B5 full-prompt rescue** in `context_enrichment.enrich()` — when B2 missed but task is writing/creative AND body has tech content, scan full prompt with `has_technical_nouns` and upgrade to `code_aware` (sets `full_prompt_technical_rescue=True`). (3) **B5+ codebase trim** — `_writing_about_code` caps codebase context at `WRITING_CODE_CONTEXT_CAP_CHARS=15000`. End-to-end live: B2 path scored 7.39 (was 6.61), B5 path 7.33 with `concision_preserved=True`. (`context_enrichment.py` + `pipeline_phases.py` + `prompts/optimize.md`)
- **C1-C5 score calibration sweep** — (C1/C5) z-score asymmetric cap with floor only at `-2.0`, ceiling uncapped — preserves legitimate above-average upside that the prior symmetric clip compressed; (C2) length budget guidance in analysis_summary based on `_orig_conc` and `_orig_len`; (C3) technical-prompt conciseness blend bumped to 0.35 so repeated domain vocabulary doesn't get TTR-penalized as verbose; (C4) `Optimization.heuristic_baseline_scores` JSON column stores deterministic `HeuristicScorer.score_prompt(raw_prompt)` snapshot distinct from LLM-blended `original_scores`; `improvement_score` derivation uses `heuristic_lift`; (C6) selective inline-backtick handling — `_looks_like_code_reference` unwraps when contents contain `/`, `_`, or source extension; pruned ambiguous brand names from `_TECHNICAL_NOUNS` (github/lambda/react/vue/kafka). (`score_blender.py` + `pipeline_phases.py` + `models.py` + `task_type_classifier.py`)
- **T1.x learning loops** — (T1.1) Bayesian shrinkage on phase-weight learning with `SCORE_ADAPTATION_PRIOR_KAPPA=8.0` + `SCORE_ADAPTATION_MIN_SAMPLES=2` replaces prior min-10 hard gate; (T1.6) MMR diversity on few-shot retrieval with `FEW_SHOT_MMR_LAMBDA=0.6`; (T1.3-lite) `OptimizationPattern.useful_count`/`unused_count` counters; (T1.7) A4 confidence gate tuned to `0.40` + `0.10` margin so Haiku fallback fires more often (~15-20%) for genuinely ambiguous prompts. (`fusion.py` + `pattern_injection.py` + `task_type_classifier.py`)
- **Migration `d3f5a8c91024` idempotency + frontend fixes** — wrapped each `add_column` in `inspector.get_columns()` guards (matching `c2d4e6f8a0b2` pattern). Topology halo + readiness ring per-frame sync inside formation animation callback. Observatory `refreshPatternDensity()` + `loadTimelineEvents()` fire from `taxonomy_changed`/`domain_created`/SSE-reconnect handlers. (`alembic/d3f5a8c91024.py` + `SemanticTopology.svelte` + `+page.svelte`)

### v0.4.6 — 2026-04-25

Self-update hardening — pre-flight endpoint + drain lock + auto-stash + per-step progress against three P0 risks (strategy edits silently lost, in-flight optimization race, local-commits-ahead-of-origin orphan). New `customization_tracker.py` records strategy edits; `GET /api/update/preflight` returns `PreflightStatus` with dirty-source classification (`user_api`/`manual_edit`/`untracked`), commits-ahead, in-flight count, detached-HEAD, target-tag presence; `UpdateInflightTracker` coordinates pipeline `begin/end(trace_id)` with `apply_update`'s drain wait (60s budget); auto-stash + pop on `prompts/`; per-step SSE events (`update_step` at preflight/drain/fetch_tags/stash/checkout/deps/migrate/pop_stash/restart/validate). Frontend rebuild: `UpdateBadge` dialog with preflight panel, dirty-file paths, in-flight counter, `Update & Restart (force)` warning variant, completion view rendering `validationChecks` + `stashPopConflicts`, retry button after timeout. 17 customization-tracker + 17 update_service tests.

### v0.4.5 — 2026-04-25

Pattern-injection provenance now writes post-commit (provenance was silently rolling back inside `begin_nested()` SAVEPOINT due to FK-failure on uncommitted parent — `auto_inject_patterns()` gains `record_provenance` flag; internal/sampling pass `False` and `record_injection_provenance()` is invoked after `db.commit()`). Enrichment profile no longer demotes async/concurrency code prompts (async/concurrency vocab + interior `.`/`-`/`/` split + snake_case/PascalCase identifier syntax detection). GitHub OAuth no longer surfaces upstream JSON failures as misleading CORS errors. Optimizer-thinking interrogative-voice no longer leaks into deliverables. Post-LLM domain reconciliation (qualifier syntax flows into `domain_raw` via `_normalize_llm_domain()`; runs BEFORE resolver). `find_best_qualifier()` tiebreaker prefers in-text qualifier name on hit-count ties. Sub-domain label canonicalization single-source-of-truth via `normalize_sub_domain_label(raw, max_len=30)`. Task-type structural-evidence rescue (creative/writing → coding when first sentence has snake_case/PascalCase/technical-noun signals; scope-guarded against analysis/data). `enrichment` summary on `/api/history` rows. Source-breakdown chip strip on `SubDomainEmergenceList`. Multi-sibling sub-domain test coverage. Observability separation between ActivityPanel (topology terminal) and DomainLifecycleTimeline (Observatory) — shared `activity-filters.ts` + `activity-summary.ts` modules.

### v0.4.4 — 2026-04-25

Shipped: ADR-007 Live Pattern Intelligence Tier 1, Taxonomy Observatory Tier 1, `since`/`until` activity-history range variant, `/api/taxonomy/pattern-density` aggregator, plus the nine audit-follow-up + roadmap-debt items below. Two PRs (#50 ContextPanel + #51 Observatory) merged to `main` as feature work; the audit follow-up landed as standalone RED→GREEN-tested commits.

- **ADR-007 Tier 1 — Live Pattern Intelligence (`ContextPanel.svelte`)** — replaces the legacy `PatternSuggestion.svelte` banner with a persistent sidebar mounted by `EditorGroups`. Cluster identity row + meta-patterns checkboxes + neon-purple-bordered GLOBAL section for cross-cluster patterns. APPLY commits multi-pattern selection to `forgeStore.appliedPatternIds`. Mount-gated to the prompt tab, hidden during synthesis, full a11y. Backend `POST /api/clusters/match` response gains additive `match_level` + `cross_cluster_patterns` keys (no schema migration). Spec: [docs/superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md](superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md). PR #50.
- **Taxonomy Observatory Tier 1 — three-panel observability tab** — pinned `OBSERVATORY` workbench tab mounting `TaxonomyObservatory.svelte`. Three panels: `DomainLifecycleTimeline` (reverse-chrono SSE-live + JSONL backfill), `DomainReadinessAggregate` (composes existing meter+emergence per domain), `PatternDensityHeatmap` (read-only data grid with hover tooltip). Period selector (`24h | 7d | 30d`) drives Timeline + Heatmap via `observatoryStore`. Backend additions: `since`/`until` range variant on `GET /api/clusters/activity/history`, new `GET /api/taxonomy/pattern-density` aggregator, `taxonomy_insights.py` service + router + Pydantic schemas. Spec: [docs/superpowers/specs/2026-04-24-taxonomy-observatory-design.md](superpowers/specs/2026-04-24-taxonomy-observatory-design.md). PR #51.
- **Post-merge spec compliance audit (PRs #50 + #51)** — five spec gaps caught during a full re-read: (1) `getClusterActivityHistory` API client missing `since`/`until` range params, (2) Timeline period chips were no-op for the Timeline panel, (3) `DomainReadinessAggregate` cards missing 6 px chromatic dot, (4) `PatternDensityHeatmap` rows missing hover affordance + tooltip, (5) Activity-history within-day events emitted oldest-first in both single-day and range modes. Each fix lands with a regression test source-locked against the contract.

The remaining v0.4.4 work shipped to `main` after the v0.4.3 cut as standalone RED→GREEN-tested commits:

- **Full doc audit against v0.4.4-dev state** — every document under `docs/` (excluding `CHANGELOG.md`) cross-referenced against the current codebase. ROADMAP, routing-architecture, embedding-architecture, sub-domain-discovery, context-injection-use-case-matrix, sampling-tier-data-processing, hybrid-taxonomy-plan, SUPPORT, the three heuristic-analyzer docs, enrichment-consolidation-action-items, the three context-depth-audit iterations, ADR-001, ADR-007 — all updated with current-state facts + version markers + (for historical records) status banners. 30 files touched, 320 insertions / 152 deletions.
- **#8 `prepare.py` preferences snapshot parity with `optimize.py`** — hoist `prefs_snapshot = prefs.load()` once and thread into every `prefs.get(key, snapshot)` call. Eliminates the redundant disk I/O + legacy-key-migration pass per `synthesis_prepare_optimization` invocation. Regression-guard test in `test_mcp_tools.py` asserts `load()` is called exactly once per call and every `prefs.get` passes the snapshot as second arg. (Commit `48c82a9d`.)
- **#11 DomainResolver confidence-aware cache TTL** — replaced the unbounded `dict[str, str]` cache with a confidence-tagged TTL cache (`_CacheEntry(resolved, confidence, expires_at)`). Low-confidence "general" collapses get a 60 s TTL so they self-heal when the warm path promotes the organic label; known-label / high-confidence preserved resolutions get 3600 s. A subsequent call with confidence ≥ cached + 0.1 (`CACHE_CONFIDENCE_UPGRADE_DELTA`) evicts the stale entry — prevents A4 Haiku-retry starvation. All 12 pre-existing tests pass unchanged; 4 new cases cover the TTL + confidence-upgrade paths. (Commit `9f886d1e`.)
- **#10 Conciseness heuristic calibration for technical prompts** — TTR penalized technical specs that repeat domain vocabulary ("pipeline", "schema", "service") despite those repetitions reflecting density, not verbosity. Observed on matched-TTR pairs: tech prompt 6.51 vs prose prompt 6.51 (zero differentiation). Fix: when `_count_technical_nouns(prompt) >= 3` (word-boundary match against `_TECHNICAL_NOUNS`), multiply raw TTR by `TECHNICAL_TTR_MULTIPLIER=1.15` before band mapping, clamped at 1.0. Three new tests lock behavior; all 30 pre-existing tests pass. (Commit `6b5a615b`.)
- **#9 E2 enrichment profile effectiveness on `/api/health`** — `phase-e-observability.md` has listed E2 as "still to be designed" since v0.3.30. The persistence layer already populated `context_sources["enrichment_meta"]["enrichment_profile"]` on every completed row; nothing consumed it. New `OptimizationService.get_enrichment_profile_effectiveness(limit=200)` aggregates Python-side (no SQL `GROUP BY` — nested JSON key, dialect-portable) and surfaces via new `HealthResponse.enrichment_effectiveness` field. Per-profile `{count, avg_overall_score, avg_improvement_score}`. NULL-improvement rows contribute to count + avg_overall_score but are skipped in the improvement average. 3 service tests + 2 endpoint tests. (Commit `e7f9e468`.)
- **#7 Code-block / markdown-table strip before first-sentence extraction** — code fences + pipe-delimited table rows at the top of a prompt polluted the `first_sentence` boundary used by the 2x positional-boost keyword scoring. New `extract_first_sentence()` helper in `task_type_classifier.py` pre-strips triple-backtick fences, inline-backtick spans, and markdown table rows before splitting on `.?!`. Applied at both call sites (heuristic_analyzer + context_enrichment). 4 new tests assert unit-granular boundary behavior — code-fence / table / inline-backtick stripping + the no-code baseline. (Commit `fff63665`.)
- **#12 A1+A2 follow-up: "design a system prompt" classifies as system** — audit of commit `14511cd3` surfaced one drift case: "Design a system prompt for evaluating design systems" classified as `coding` because `design a system` (coding 1.3) left-substring-matched without a longer compound to break the tie. Fix: three compounds at weight 1.5 on the `system` task_type (`design a system prompt`, `build a system prompt`, `create a system prompt`). 7 new tests pin mixed compound + single-word signal interactions (`system prompt` → system, plain `design a system` → coding retained, meta-prompt + debug intent → system, diagnose + recommend → analysis, design-a-pipeline → coding). (Commit `124d5cf7`.)
- **#1 Sampling fallback classifier alignment with DomainSignalLoader** — `build_analysis_from_text()` in `sampling/primitives.py` (the analyze-phase last-resort when IDE sampling response can't be parsed) maintained its own hardcoded `type_keywords` + `domain_keywords` dicts since v0.3.32, drifting silently from the organic warm-path pipeline (`_TASK_TYPE_SIGNALS` + `DomainSignalLoader`). Replaced with `classify_task_type(combined, extract_first_sentence(combined), get_task_type_signals())` + `DomainSignalLoader.score(words)` + `.classify(scored)`. Graceful `None`-loader fallback to `"general"` for startup / test contexts. 6 new tests pin the delegation + end-to-end dynamic-signal flow-through. (Commit `7f41f870`.)
- **#3 `signal_adjuster.py` — TaskTypeTelemetry consumer (C2)** — since v0.4.2 the A4 Haiku fallback has persisted every ambiguous-prompt classification to `TaskTypeTelemetry`; nothing consumed those rows. New active-learning oracle reads the last 7 days (`SIGNAL_ADJUSTER_LOOKBACK_DAYS`), tokenizes prompts, tallies `(token, task_type)` pairs, and merges tokens that cross `SIGNAL_ADJUSTER_MIN_FREQUENCY=3` hits into `_TASK_TYPE_SIGNALS[task_type]` at `SIGNAL_ADJUSTER_WEIGHT=0.5`. Only ADDS novel tokens — never overwrites existing weights. Emits one `signal_adjusted` taxonomy event per merged token. Wired as Phase 4.76 in warm path (runs after Phase 4.75 TF-IDF extraction so active-learning additions layer on top). 8 new tests + non-fatal-on-missing-table + non-fatal-on-missing-logger degradation. (Commit `b8159a70`.)
- **#6 Non-developer vertical — seed agents + domain keyword migration (ADR-006 content-first playbook step 1)** — two new seed agents (`marketing-copy.md`, `business-writing.md`) + Alembic migration `c2d4e6f8a0b2` seeding three new domain nodes with brand-aligned OKLab colors + keyword signals: `marketing` (#ff7a45, 16 keywords), `business` (#3fb0a9, 18 keywords), `content` (#f5a623, 17 keywords). Each seed carries `cluster_metadata.vertical="non-developer"` for ADR-006 traceability + safe downgrade. Idempotent via `existing_labels` guard. 6 new tests cover agent loading, loader integration, migration idempotency, and the vertical marker. Zero engine code changes — pure content additions proving ADR-006's universal-engine claim. (Commit `a217c6cf`.)

### v0.4.3 — 2026-04-24
- **Bulk delete REST + History UX** — `POST /api/optimizations/delete` (1-100 ids, 10/min rate-limited, `DeleteOptimizationsResponse` envelope) + single-row `DELETE /api/optimizations/{id}` both route through `OptimizationService.delete_optimizations()`. Frontend: hover × with 5 s `UndoToast` grace window (commit deferred until timer expires), opt-in multi-select mode with `Select`/`Cancel`/`Delete N` toolbar, `DestructiveConfirmModal` with type-to-confirm (`DELETE` literal), keyboard shortcuts (Ctrl/Cmd+Click auto-seed, Shift+Click range, Ctrl+A, Esc, Delete/Backspace, arrow nav), bulk-to-single graceful fallback.
- **Reusable destructive-action primitives** — `toastsStore` singleton with pre-commit grace hook (`commit?: () => Promise<void>`), `UndoToast` (`scaleX` transform for compositor-only RAF repaints, pause-on-hover, `aria-live="polite"`), `DestructiveConfirmModal` (glass panel, `role="dialog"`, case-sensitive literal gate, focus return on cancel). All respect the brand zero-effects directive + `prefers-reduced-motion`.
- **Frontend brand-guidelines strict audit — zero violations** — removed stray `text-shadow` rules, consolidated remaining `border-radius: 4px` surfaces to `0`, replaced `box-shadow` with 1px inset contour, purged `--glow-*` refs from legacy comments.
- **Frontend consumes `optimization_deleted` SSE** — event has shipped since v0.4.2 but had no UI handler; `+page.svelte` bridges to `CustomEvent('optimization-deleted')`, `HistoryPanel` removes matching row surgically (no full re-fetch), 2 s fallback timeout covers SSE reconnect gaps.
- **Delete endpoints use `BASE_URL`** — `frontend/src/lib/api/optimizations.ts` routes through shared `apiFetch` so dev→prod port drift doesn't silently 404 the delete surface.
- **`task_type_signal_extractor` graceful degradation** — INSERT into `task_type_telemetry` wrapped in `try/except OperationalError`; unmigrated DBs get warn-log once/cycle instead of crashing the warm path (was leaving `member_count` stale on domain nodes after deletes).
- **Test isolation helpers** — public `reset_rate_limit_storage()` + shared `drain_events_nonblocking()` promoted from per-file spelunking to `conftest.py` / `dependencies/rate_limit.py`.

### v0.4.2 — 2026-04-23
- **MCP sampling architecture unification + Hybrid Phase Routing** — `MCPSamplingProvider` now a first-class `LLMProvider`; 1,700-line redundant sampling pipeline collapsed to re-export layer over the primary orchestrator. Hybrid Execution Routing: fast phases (analyze, score, suggest) stay on internal provider; optimize routes through the IDE LLM. MCP transport errors map to `ProviderError` so Tenacity retries apply. `StreamableHTTPServerTransport` patched to extract TS SDK `sessionId` from query params.
- **`TaskTypeTelemetry` model + migration `2f3b0645e24d`** — records heuristic vs LLM classification events (`raw_prompt`, `task_type`, `domain`, `source`) for drift analysis + A4 tuning.
- **Inspector analyzer telemetry rendering (UI2)** — ENRICHMENT panel surfaces signal-source tag (bootstrap/dynamic), TASK-TYPE SCORES distribution, CONTEXT INJECTION counts. `build_pipeline_result()` propagates `inputs.repo_full_name` into `PipelineResult.repo_full_name`.
- **`enrichment_meta.injection_stats` uniform emission (UI1) + AA1 auto-bind** — every tier emits `{patterns_injected, injection_clusters, has_explicit_patterns}`. `project_service.resolve_effective_repo()` resolves repo via explicit → session-cookie → most-recently-linked cascade so curl / session-less API callers bind to the live `LinkedRepo`'s project instead of falling through to Legacy.
- **Explicit DOMAIN SIGNALS + RETRIEVAL headings + CLI-family classifier coverage (A8)** — `cli`/`daemon`/`binary` added to `_TECHNICAL_NOUNS` (A2) and coding-signal keywords at moderate weights.
- **`DELETE /api/optimizations/{id}` REST + `synthesis_delete` MCP tool (audit #3 + #4)** — thin wrapper over long-existing `delete_optimizations()` primitive; unknown id now 404s.
- **`POST /api/taxonomy/reset` admin recovery (I-0)** — force-prunes archived zero-member clusters + delegates to `run_warm_path` synchronously; idempotent.
- **`taxonomy_changed` SSE publish on bulk delete (I-0)** — cross-process dirty-set bridge fires warm Phase 0 immediately instead of waiting for 30 s debounce.
- **Inspector per-layer enrichment skip reason (I-9)** — renders right-aligned reason tags ("skipped — cold start profile", "deferred to pipeline").
- **Tree integrity repair SSE events (I-8)** — `tree_integrity_repair` emits per repair with `{violation_type, action, label}`.
- **`OptimizationService.delete_optimizations(ids, *, reason)` bulk primitive** — relies on DB `ondelete="CASCADE"` (migration `a2f6d8e31b09`), emits per-row `optimization_deleted` events + aggregated `taxonomy_changed`. Migration `b3a7e9f4c2d1` dedupes orphan unnamed FKs.
- **Warm Phase 0 clears stale `learned_phase_weights` on empty clusters** — prevents "phantom learning" if cluster id is reused within 24h archival window.
- **Provider threaded through enrichment + tool handlers** — `HeuristicAnalyzer.analyze()` + `ContextEnrichmentService.enrich()` accept `provider` kwarg at every call site; A4 Haiku fallback resolves without global lookup.
- **Negation-aware weakness detection + signal-source accuracy** — `_is_negated()` + `_compute_structural_density()`; `_TASK_TYPE_EXTRACTED` set distinguishes `bootstrap` from `dynamic` honestly. Legacy `static` value accepted as read-compat synonym for one release cycle.
- **Analyze phase effort clamp to `high` ceiling (A3)** — `ANALYZE_EFFORT_CEILING='high'` applied at all three analyze call sites; `max`/`xhigh` downshift to `high`. Does NOT apply to optimize/score. Expected drop: 200+s → 30–60s.
- **`auto → strategy` routed by `intent_label` (A2)** — new step 5b in `resolve_effective_strategy` inspects `intent_label` for chain-of-thought (audit/debug/diagnose), structured-output (extract/classify/list), role-playing (story/poem/narrative) keywords. Fires when current strategy equals the task-type default too (not only literal `"auto"`).
- **`enrichment_meta.domain_signals` shape — `{resolved, score, runner_up}`** — winner named explicitly; `reconcile_domain_signals()` rebuilds after pipeline finalizes domain; runner_up emits only when `best_runner <= top_score AND > 0`.
- **`/api/health` surfaces `taxonomy_index_size` + `avg_vocab_quality`** — Plan I-1 wired boot logging but health fields were stuck at `None`; now pulled off `app.state.taxonomy_engine`.
- **`Q_system` / `Q_health` return `None` with fewer than 2 active clusters (A5)** — single-node taxonomies no longer report perfect scores; Q-gates treat transitions as growth/destruction/no-progress.
- **Routing: REST callers excluded from sampling, internal beats auto-sampling** — `_can_sample()` narrowed to `caller == "mcp"`; auto path tries tier 3 internal before tier 4 auto-sampling.
- **`_write_optimistic_session` preserves `sampling_capable`** — no longer forces `True` on session-less reconnects from plain Claude Code.
- **Cross-process `taxonomy_changed` bridged into engine dirty_set** — `_apply_cross_process_dirty_marks(engine, event_data)` runs before `_warm_path_pending.set()` so MCP/CLI deletes reconcile immediately.
- **Classifier B1/B2/B6 fixes** — SQLAlchemy/FastAPI/Django nouns added to coding signals + `_TECHNICAL_NOUNS`; `has_technical_nouns(first_sentence)` rescues any task_type to `code_aware` when repo linked; `_STATIC_SINGLE_SIGNALS` preserves single-word defaults through dynamic merges.
- **Pattern injection — unscoped clusters visible inside project filter (A10)** — `embedding_index.search()` treats `project_ids[label]=None` as "unreconciled, visible within any scope" (brand-new pre-Phase-0 clusters).
- **Pattern injection provenance — `begin_nested()` SAVEPOINT** — replaces manual expunge; prevents `PendingRollbackError` from cascading into subsequent pipeline phases on FK IntegrityError.

### v0.4.1 — 2026-04-20
- **Sidebar brand audit finale — Navigator 2,692 → 182 lines** — 8-panel extraction (`StrategiesPanel`, `HistoryPanel`, `GitHubPanel`, `SettingsPanel`, `ClusterRow`, `DomainGroup`, `StateFilterTabs`, `TemplatesSection`). `CollapsibleSectionHeader` gains Snippet-based whole-bar/split modes. `ActivityBar` sliding indicator, Inspector phase-dot.
- **Inspector.svelte split — 3 sections extracted** — `ClusterPatternsSection` (103 l), `ClusterTemplatesSection` (70 l, disambiguated from Navigator's proven-templates section), `TaxonomyHealthPanel` (123 l). Inspector 1,404 → 1,165 l.
- **Backend Phase 3 refactor split (A-F)** — six module-boundary extractions: Phase 3A (`context_enrichment.py` 1,394 → 3 modules: `repo_relevance.py`, `divergence_detector.py`, `strategy_intelligence.py`), Phase 3B (`sampling_pipeline.py` 1,705 → 3 sub-modules: `sampling/primitives.py`, `sampling/persistence.py`, `sampling/analyze.py`), Phase 3C (`repo_index_service.py` 1,676 → `repo_index_outlines.py`, `repo_index_file_reader.py`, `repo_index_query.py`), Phase 3D (`pipeline.py` 1,146 → 610, 12 pure helpers in `pipeline_phases.py`), Phase 3E (`batch_pipeline.py` 1,077 → `batch_orchestrator.py` + `batch_persistence.py`), Phase 3F (`heuristic_analyzer.py` 929 → thin orchestrator + `task_type_classifier.py` + `domain_detector.py` + `weakness_detector.py`). All preserve public API via re-exports.
- **UI persistence through stores (code-quality sweep Phase 2)** — `githubStore.uiTab`, `stores/hints.svelte.ts`, `stores/topology-cache.svelte.ts` replace ad-hoc `$effect` + direct localStorage in `GitHubPanel`, `TopologyControls`, `SemanticTopology`. One-shot migration shims preserve user state.
- **`utils/keyboard.ts` + `utils/transitions.ts`** — pure `nextTablistValue()` + `handleTablistArrowKeys()` for tablist arrow-key nav; `navSlide`/`navFade` presets driven by inline 8-iteration Newton-Raphson bezier solver matching `--ease-spring` exactly (Svelte's built-in `cubicOut` drifted visibly).
- **PRAGMA event hook on every pool checkout** — `@event.listens_for(engine.sync_engine, "connect")` applies WAL + busy_timeout + synchronous + cache_size + foreign_keys to every SQLite pool connection. Replaces throwaway single-connection aiosqlite block. `pool_pre_ping=True` + `pool_recycle=3600` restored.
- **Recurring GC sweep — hourly expired-token + orphan-repo cleanup** — `run_recurring_gc()` + `_recurring_gc_task` scheduled in lifespan. Sweeps expired `GitHubToken` (24 h grace) + orphan `LinkedRepo` rows. Previously accumulated indefinitely between restarts.
- **Hotpath indices migration `cc9c44e78f78`** — seven single-column indices on `optimizations` + composite `ix_optimizations_project_created(project_id, created_at DESC)` + `ix_feedbacks_optimization_id`.
- **Soft-delete retirement documented** — v2 rebuild excised the `deleted_at` column set; archive-as-soft-delete via `cluster.state='archived'` covers legitimate undelete cases; hard-delete simpler for GDPR.

### v0.4.0 — 2026-04-19
- **ADR-005 Hybrid Taxonomy — projects as sibling roots (8-commit shipment)** — supersedes original "project as tree parent" data model. Projects at `parent_id IS NULL` alongside domain nodes; clusters parent to domains and carry `dominant_project_id` FK. S1 migration + B1 pipeline freezes `project_id` at request time via `resolve_project_id()` + B2-B5 `POST /api/projects/migrate` rate-limited + link/unlink `mode=keep|rehome` + B6 tree/stats SQL-scoped by `dominant_project_id` + B7-B8 pattern filtering + dual-gate global promotion (`GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS=5` + `MIN_PROJECTS=2`) + C1 warm/cold maintenance + F1-F5 frontend (projectStore, project selector, explicit `project_id` threading, transition toasts, per-project Inspector breakdown). Locked-decision record: `docs/hybrid-taxonomy-plan.md`. ADR-005 Amendment 2026-04-19 links out. Phase 3 HNSW stays trigger-gated.
- **Opus 4.7 provider feature surface** — `xhigh` effort level (Opus 4.7 only, `xhigh → high` downgrade with warning on other models), `display: "summarized"` adaptive thinking (was silent on Opus 4.7 which defaults to `omitted`), Task Budgets beta (`task_budget: int | None`, 20k min clamp, `task-budgets-2026-03-13` header), Compaction beta (`compaction: bool`, Opus 4.7/4.6 + Sonnet 4.6 only, `compact-2026-01-12` header). Combined betas comma-joined into single `extra_headers["anthropic-beta"]`. `ClaudeCLIProvider` accepts both as documented no-ops for ABC uniformity. `LLMProvider.supports_xhigh_effort(model)` static helper centralizes the gate. 11 new provider tests + 3 preferences tests.
- **`synthesis_health` `linked_repo` block** — returns `full_name`, `branch`, `language`, `index_status`, `index_phase`, `files_indexed`, `synthesis_ready` for the active linked repo. Single MCP health call now confirms codebase-context availability end-to-end.
- **Per-agent seed model override + per-dispatch JSONL trace** — seed agents default to Haiku; can opt into Sonnet/Opus via YAML frontmatter `model:`. `SeedAgent.model` added; `_resolve_agent_model()` maps frontmatter → `settings.MODEL_*`. Each dispatch emits `phase="seed_agent"` trace with `trace_id="seed:{batch_id}:{agent}"`, duration, tokens, resolved model.
- **Explore synthesis routed to Sonnet + JSONL trace** — `CodebaseExplorer._explore_inner` synthesizes 30-80K-token file payloads; long-context reading favors Sonnet. Cached per repo/branch/SHA in `RepoIndexMeta.explore_synthesis` so cost delta is negligible. Per-run `phase="explore_synthesis"` trace.
- **Phase 0 orphan-structural-node sweep** — warm-path reconciles empty domain / sub-domain nodes with 0 active-cluster children AND 0 sub-domain children AND 0 optimization references, gated on `ORPHAN_STRUCTURAL_GRACE_HOURS=24`. Fixes "ghost Legacy 1m 0 --" visibility bug where ADR-005 migration left empty `general` domain inflating `member_count=1` forever. 5 RED→GREEN tests.
- **Heuristic classifier — audit verbs + sentence boundary + compound keywords** — added `audit`/`diagnose`/`inspect` to analysis signals; first-sentence boundary uses `re.split(r"[.?!]", ...)` (was `.split(".")`); compound-phrase signals (`"write a prompt"` → system, `"audits the"` → analysis) outweigh single-word collisions.
- **B0 repo-relevance gate — project-anchored synthesis + path-enriched anchor** — anchor embedding prepends `Project: {repo_full_name}\n` + appends up to 500 indexed file paths as `Components:` block (stride-sampled at 100 for MiniLM 512-tok window). `REPO_RELEVANCE_FLOOR` 0.20 → 0.15; `REPO_DOMAIN_MIN_OVERLAP` removed. Reason codes collapse to `{above_floor, below_floor}`. Same-stack-different-project separation rides on project-name signal, not vocabulary overlap. `extract_domain_vocab()` retained for UI attribution only.
- **Background task GC race fix — strong-ref holder** — `_background_tasks: set[asyncio.Task]` + `_spawn_bg_task()` helper prevents weak-ref GC mid-await on `link_repo` / `reindex` (was silently killing `synthesis_status='running'` jobs).
- **Inspector "no codebase context" warning chip** — yellow chip renders when `activeResult.repo_full_name` is set AND `context_sources.codebase_context === false`. Recommends reindex + rerun.
- **Cluster navigator MBR column semantic suffix** — `Nd` (domains, project rows), `Nc` (clusters, domain rows), `Nm` (members, cluster rows). `member_count` is semantically overloaded across node types.
- **`DomainResolver` preserve high-confidence unknown labels + gate lowered 0.6 → 0.5** — resolver no longer collapses unknowns to `general` pre-emptively; warm-path domain discovery depends on organic labels reaching the engine.
- **Seed palette preservation on domain re-promotion** — `SEED_PALETTE` mirrors alembic `SEED_DOMAINS`; `engine._create_domain_node()` restores canonical color on re-promotion if not already in use. Empty seed domains still dissolve per ADR-006, but "Backend is purple" survives dissolution cycles.
- **A4 LLM classification wrapped in retry** — wrapped with `call_provider_with_retry` so transient rate-limit/overload errors retry before degrading to heuristic result.

### Templates entity + fork-on-promotion (v0.3.39)
Immutable `PromptTemplate` rows forked from mature clusters crossing fork thresholds. Source cluster stays `mature` and keeps learning. Warm Phase 0 reconciles `template_count` + auto-retires templates whose source degrades (avg_score < 6.0) or is archived. Templates router (`GET /api/templates`, `POST /api/clusters/{id}/fork-template`, `POST /api/templates/{id}/retire`, `POST /api/templates/{id}/use`). Partial-unique constraint on `(source_cluster_id, source_optimization_id) WHERE retired_at IS NULL`. Halo rendering on 3D topology (`template_count > 0` → 1px contour ring). Navigator PROVEN TEMPLATES group + Inspector collapsible section. `.claude/hooks/pre-pr-template-guard.sh` blocks residual `state='template'` literals. Deprecates the old `state='template'` enum path (410/400 on legacy routes).

### GitHub indexing pipeline with caching (v0.3.40)
Four coordinated caches cut GitHub API pressure and embedder cost: tree ETag conditional fetches, content-hash embedding dedup across branches/repos, file-content TTL+FIFO cache (keyed by blob SHA, branch-independent), curated retrieval invalidation on rebuild. Per-phase `index_phase` column (`pending → fetching_tree → embedding → synthesizing → ready|error`) with `index_phase_changed` SSE events. Frontend `connectionState` expanded to 7 states; phase-aware Navigator badge + pulse animation + error row. Preference key rename `enable_adaptation` → `enable_strategy_intelligence` (+ lazy migration). Shared file-exclusion filters (`file_filters.py`) between repo-index and codebase-explorer.

### Domain readiness telemetry + sparklines + topology overlay (v0.3.37 → v0.3.38 → v0.3.39)
`GET /api/domains/readiness` + `/api/domains/{id}/readiness` with 30s TTL cache; three-source cascade primitive shared with `_propose_sub_domains()` for zero-drift. `DomainStabilityMeter`, `SubDomainEmergenceList`, `DomainReadinessPanel` UI with per-row mute bells + master mute. Readiness snapshot writer (`readiness_history.py`) with JSONL daily rotation + 30-day retention. `GET /api/domains/{id}/readiness/history?window=24h|7d|30d` with hourly bucketing beyond 7d. `DomainReadinessSparkline` peer component with window selector (persisted to localStorage). Tier-crossing detector with 2-cycle hysteresis + per-domain cooldown publishes `domain_readiness_changed` SSE gated by `domain_readiness_notifications` preference (default on). Topology overlay: per-domain readiness ring with composite tier coloring (`composeReadinessTier()`), billboard orientation, LOD attenuation, cubic-bezier tier transitions, `prefersReducedMotion()` awareness. 1142+ frontend tests passing (brand-guard + behavioral).

### Opus 4.7 default + mypy strict cleanup (v0.3.37)
`MODEL_OPUS` default flipped to `claude-opus-4-7` (1M-token native context). Full mypy strict cleanup: 103 → 0 errors across 133 source files; `backend/app/models.py` refactored to SQLAlchemy 2.0 `Mapped[]` typed declarative columns.

### Enrichment engine consolidation + heuristic accuracy (v0.3.30)
Unified context enrichment with auto-selected profiles (`code_aware` / `knowledge_work` / `cold_start`), task-gated curated retrieval, strategy intelligence merge, workspace guidance collapse. Heuristic accuracy pipeline: compound keywords (A1), verb+noun disambiguation (A2), TF-IDF domain signal auto-enrichment (A3), confidence-gated Haiku fallback (A4). Prompt-context divergence detection (B1+B2), domain-relaxed fallback queries (C1), classification agreement tracking (E1). 2107 backend tests. Full spec: [`enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md).

### Hierarchical edge system (v0.3.30)
Curved edge bundling in 3D topology with depth-based attenuation shader, density-adaptive opacity, proximity suppression, focus-reveal on hover, domain-colored edges. 5-phase hierarchical edge declutter.

### Injection effectiveness + orphan recovery + project-node UX (v0.3.29)
Warm-path Phase 4 measures mean score lift for pattern-injected vs non-injected optimizations. Orphan recovery with exponential backoff. Project node dodecahedron geometry + rich Inspector mode.

### SSE health + incremental refresh + per-project scheduling (v0.3.28)
Real-time SSE latency tracking (p50/p95/p99), degradation detection, exponential backoff reconnection. Repo index incremental refresh via SHA comparison. Per-project scheduler budgets with proportional quotas.

### Full source context + import graph + curated retrieval (v0.3.27)
Curated retrieval delivers actual file source code (not outlines). Import-graph expansion, test file exclusion, cross-domain noise filter, performance signals, context diagnostic panel. Skip-and-continue budget packing, source-type soft caps.

### Alembic migration for domain nodes (v0.3.8-dev)
Idempotent migration `a1b2c3d4e5f6`: adds `cluster_metadata` column, `ix_prompt_cluster_state_label` index, `uq_prompt_cluster_domain_label` partial unique index, seeds 7 domain nodes with keyword metadata, re-parents existing clusters, backfills `Optimization.domain`. Async env.py commit for DML persistence.

### Unified domain taxonomy — ADR-004 (v0.3.8-dev)
Domains are `PromptCluster` nodes with `state="domain"`. Replaces all hardcoded domain constants (`VALID_DOMAINS`, `DOMAIN_COLORS`, `KNOWN_DOMAINS`, `_DOMAIN_SIGNALS`). `DomainResolver` and `DomainSignalLoader` provide cached DB-driven resolution. Warm path discovers new domains organically from coherent "general" sub-populations. Five stability guardrails, tree integrity with auto-repair, stats cache with trend tracking. Supersedes the planned "Multi-label domain classification" item. See [`adr/ADR-004-unified-domain-taxonomy.md`](adr/ADR-004-unified-domain-taxonomy.md).

### Multi-dimensional domain classification (v0.3.7-dev)
LLM analyze prompt and heuristic analyzer output "primary: qualifier" format (e.g., "backend: security"). Taxonomy clustering, Pattern Graph edges, and color resolution parse the primary for comparison while preserving qualifier for display. Zero schema changes.

### Zero-LLM heuristic suggestions (v0.3.6-dev)
Deterministic suggestions from weakness analysis, score dimensions, and strategy context for the passthrough tier. 18 unit tests.

### Structural pattern extraction (v0.3.6-dev)
Zero-LLM meta-pattern extraction via score delta detection and structural regex. Passthrough results now contribute patterns to the taxonomy knowledge graph.

### Process-level singleton RoutingManager (v0.3.6-dev)
Fixed 6 routing tier bugs caused by per-session RoutingManager replacement in FastMCP's Streamable HTTP transport.

### Inspector metadata parity (v0.3.6-dev)
All tiers now show provider, scoring mode, model, suggestions, changes, domain, and duration in the Inspector panel.

### Electric neon domain palette (v0.3.6-dev)
Domain colors overhauled to vibrant neon tones with zero overlap to tier accent colors. Sharp wireframe contour nodes matching the brand's zero-effects directive.
