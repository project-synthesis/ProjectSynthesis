# Changelog

All notable changes to Project Synthesis. Format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added
- **ADR-005 Phase 2A: cold path project_ids rebuild + topology endpoints** — cold path now resolves dominant project_id per cluster and passes to embedding index rebuild. Tree endpoint accepts `project_id` query param for sub-tree filtering. Cluster detail returns `member_counts_by_project` breakdown. Health endpoint reports `project_count`
- **ADR-005 Phase 1: taxonomy scaling architecture** — foundational infrastructure for multi-project support
  - Centralized `EXCLUDED_STRUCTURAL_STATES` constant replacing 37+ inline state exclusion patterns across 8 taxonomy files
  - `GlobalPattern` model for durable cross-project patterns that survive cluster archival
  - `project_id` column on `Optimization` for per-project filtering (denormalized FK)
  - Legacy project node migration: existing domain nodes re-parented under a "Legacy" project hierarchy on startup
  - Dirty-set tracking on taxonomy engine: warm path split/merge phases only process clusters that changed since last cycle
  - Adaptive scheduler measurement infrastructure: rolling window of warm cycle timings with self-tuning p75 target duration
  - `project_filter` parameter on `EmbeddingIndex.search()` for per-project vector filtering with backward-compatible cache format

### Fixed
- **Observability audit: 50+ silent failure paths instrumented** — systematic audit of all taxonomy hot/warm/cold paths, pipeline phases, pattern injection, batch seeding, and trace logging. Added `logger.warning()` to every silent `np.frombuffer` embedding deserialization catch across 12 files. Promoted `q_health` computation failures, breadcrumb build failures, merge-back detection failures, and pattern extraction failures from silent/debug to warning level. Added event bus publish failure logging where previously swallowed
- **Pipeline embedding failure now logged** — `pipeline.py` embedding service failure was `except Exception: pass` (no log), losing visibility into why downstream pattern injection and few-shot retrieval degrade. Now warns with trace_id
- **Pattern injection silent drops now visible** — `np.frombuffer` failures in cross-cluster injection and few-shot retrieval silently skipped patterns/examples. Now logs warning per dropped item with trace_id
- **Sampling pipeline structured fallback now monitored** — when IDE client doesn't support tool calling and pipeline falls back to text+schema, now emits `optimization_status` event with `phase=structured_fallback` for frequency monitoring
- **Trace and event JSONL readers now warn on malformed lines** — `trace_logger.py` and `event_logger.py` silently skipped unparseable JSONL lines. Now logs warning per malformed line with filename
- **Event logger singleton warnings rate-limited** — `get_event_logger()` now emits up to 5 warnings when called before initialization, making outages detectable through Python logs instead of completely silent
- **Dissolution cascade cross-domain contamination** — `_reassign_to_active()` now prefers same-domain target clusters, preventing dissolved members from spraying cross-domain and creating junk-drawer clusters. Updates `opt.domain` on cross-domain reassignment for data consistency
- **Lifecycle dead zone for mid-size clusters** — lowered `SPLIT_MIN_MEMBERS` 25->12 and raised `FORCED_SPLIT_COHERENCE_FLOOR` 0.25->0.35, closing the gap where clusters with 6-24 members and coherence 0.25-0.50 had no lifecycle operation available

### Added
- **Split failure events** — `split/insufficient_members` and `split/too_few_children` decision events now logged when split fails due to corrupt embeddings dropping members below threshold or fewer than 2 viable children after label generation
- **Sparkline oscillation fix** — sparkline was alternating between `q_health` (0.66) and `q_system` (0.78) every time a rejected cold path snapshot appeared. Cold path rejection snapshots now carry forward the last known `q_health` instead of `None`, and sparkline filters `q_health`-only values instead of falling back to `q_system` (different metric, different scale)
- **Cross-service health probes** — `GET /api/health` now probes all three services (backend, frontend, MCP) and cross-service links (frontend→backend, MCP→backend) with 5s per-probe timeout. Returns `services` and `cross_service` dicts with status/latency/error. Status logic: `healthy`/`degraded`/`unhealthy` with 503 on unhealthy
- **Monitoring data export** — new `GET /api/monitoring` endpoint with backend/frontend/MCP uptimes, cold start latency (ms), and per-phase LLM latency percentiles (p50/p95) computed from trace JSONL data with 60s cache
- **Structured error logging** — new `ErrorLogger` writing to `data/errors/errors-YYYY-MM-DD.jsonl` with 30-day rotation. Global FastAPI exception handler captures unhandled 500s with request context. Wired into pipeline failure handler and MCP server
- **Sampling regression test suite** — 20 pytest cases in `test_sampling_regressions.py` covering all 7 known bugs: GENERAL classification upgrade, scorer heuristic fallback, meta-header cleanup, changes rationale split, clarity heuristic false positives, self-referencing cluster detection, GitHub auth guard
- **init.sh graceful retry** — services that fail readiness get up to 3 retries with exponential backoff (2s/4s/8s). Failed service name + last log line printed on final failure. Successfully started services remain running
- **Cross-domain outlier reconciliation** — Phase 0 reconciliation now ejects members whose domain differs from their cluster's domain when cosine similarity to centroid is below 0.40 and a better same-domain cluster exists. Caps at 5 ejections per cluster per cycle

### Changed
- **Logging level consistency normalized** — merge-back detection failure promoted from debug to warning (loses split loop prevention data). Pattern extraction inner-loop failures promoted from debug to warning (loses taxonomy refresh data). Batch pipeline domain resolver failure changed from silent to warning
- **Analyzer "saas" domain classification tightened** — "saas" moved from generic fallback example to explicit decision rule with clear criteria (subscription management, tenant isolation, onboarding flows, usage metering). Added clarifying instruction: classify by what the prompt asks to build, not business context

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
