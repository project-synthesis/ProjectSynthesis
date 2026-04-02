# Backend — Internal Reference

Everything backend developers need. For project overview, see root `CLAUDE.md`. **Keep this file under 200 lines.**

## Layer rules

- `routers/` → `services/` → `models/` only. Services must never import from routers.
- `PromptLoader.load()` for static templates (no variables). `PromptLoader.render()` for templates with `{{variables}}`.
- `AnalysisResult.task_type` is a `Literal`: `coding`, `writing`, `analysis`, `creative`, `data`, `system`, `general`. `selected_strategy` is a plain `str` validated at runtime against `prompts/strategies/`. `intent_label` (3-6 words) and `domain` (resolved via `DomainResolver`) default to `"general"`.

## Key services (`app/services/`)

**Pipeline**: `pipeline.py` (3-phase orchestrator), `sampling_pipeline.py` (MCP sampling — full parity), `passthrough.py` (shared passthrough assembly), `pipeline_constants.py` (shared constants: `CONFIDENCE_GATE=0.7`, `FALLBACK_STRATEGY="auto"`, `semantic_upgrade_general()` post-LLM classification gate)
**Analysis**: `heuristic_analyzer.py` (zero-LLM classifier, 6-layer, adaptive keyword weights), `context_enrichment.py` (unified `enrich()` for all tiers → frozen `EnrichedContext`), `context_resolver.py` (per-source char caps, untrusted wrapping)
**Scoring**: `heuristic_scorer.py` (5-dimension heuristics, `score_prompt()` facade, clamped [1.0, 10.0]), `score_blender.py` (hybrid LLM+heuristic, z-score normalization, divergence detection)
**Optimization**: `optimization_service.py` (CRUD, sort/filter, `VALID_SORT_COLUMNS`), `refinement_service.py` (sessions, branching, rollback, suggestions)
**Prompts & Strategies**: `prompt_loader.py` (template loading, startup validation), `strategy_loader.py` (file discovery, YAML frontmatter, hot-reload), `file_watcher.py` (watchfiles.awatch, publishes `strategy_changed`)
**Routing**: `routing.py` (RoutingManager singleton, `resolve_route()` pure function — see Routing Internals below)
**Taxonomy**: `taxonomy/` package (see Taxonomy Engine below)
**Workspace**: `workspace_intelligence.py` (manifest-based stack detection + deep scanning), `roots_scanner.py` (agent guidance file discovery, SHA256 dedup), `codebase_explorer.py` (semantic retrieval + Haiku synthesis, SHA cache), `explore_cache.py` (TTL+LRU), `repo_index_service.py` (background indexing, `query_curated_context()`)
**Embeddings**: `embedding_service.py` (singleton `all-MiniLM-L6-v2`, 384-dim), `embedding_index.py` (numpy index, O(1) upsert, batch cosine)
**Domain**: `domain_resolver.py` (cached DB lookup, replaces `VALID_DOMAINS`), `domain_signal_loader.py` (keyword signals from domain metadata)
**Patterns**: `pattern_injection.py` (`auto_inject_patterns()` with composite fusion + cross-cluster injection), `prompt_lifecycle.py` (state promotion, quality pruning, usage decay, orphan backfill)
**Infrastructure**: `event_bus.py` (in-process pub/sub), `event_notification.py` (cross-process HTTP POST), `trace_logger.py` (per-phase JSONL, daily rotation), `mcp_session_file.py` (read/write/staleness), `mcp_proxy.py` (REST→MCP sampling proxy)
**Feedback**: `feedback_service.py` (CRUD + adaptation update + phase weight adaptation), `adaptation_tracker.py` (strategy affinity, degenerate detection, phase weight EMA)
**GitHub**: `github_service.py` (Fernet encrypt/decrypt), `github_client.py` (raw API, explicit token param)
**Preferences**: `preferences.py` (file-based JSON, frozen snapshot per pipeline run, effort levels: `low`|`medium`|`high`|`max`)

## Model configuration

Model IDs centralized in `config.py`: `MODEL_SONNET` (`claude-sonnet-4-6`), `MODEL_OPUS` (`claude-opus-4-6`), `MODEL_HAIKU` (`claude-haiku-4-5`). Never hardcode — use `PreferencesService.resolve_model(phase, snapshot)`.

## Providers (`app/providers/`)

- `base.py` — `LLMProvider` ABC: `complete_parsed()`, `complete_parsed_streaming()`, `thinking_config()`, `call_provider_with_retry()`
- `detector.py` — auto-selects: Claude CLI → Anthropic API. Detected **once at startup**, stored on `app.state.routing`
- `claude_cli.py` — CLI subprocess (zero marginal cost). Gates `--effort` for Haiku
- `anthropic_api.py` — SDK with prompt caching (`cache_control: ephemeral`), streaming, `max_retries=0` (app-level retry only)

## Routers (`app/routers/`)

| Router | Endpoints |
|--------|-----------|
| `optimize.py` | `POST /api/optimize` (SSE), `GET /api/optimize/{trace_id}` |
| `history.py` | `GET /api/history` (sort/filter, pagination envelope) |
| `feedback.py` | `POST /api/feedback`, `GET /api/feedback?optimization_id=X` |
| `refinement.py` | `POST /api/refine` (SSE), `GET /api/refine/{id}/versions`, `POST /api/refine/{id}/rollback` |
| `providers.py` | `GET /api/providers`, `GET/PATCH/DELETE /api/provider/api-key` |
| `preferences.py` | `GET/PATCH /api/preferences` |
| `strategies.py` | `GET /api/strategies`, `GET /api/strategies/{name}`, `PUT /api/strategies/{name}` |
| `settings.py` | `GET /api/settings` (read-only) |
| `github_auth.py` | OAuth: login, callback, me, logout |
| `github_repos.py` | `GET /api/repos`, link, linked, unlink |
| `health.py` | `GET /api/health` (provider, tiers, scores, errors, domain_count) |
| `events.py` | `GET /api/events` (SSE), `POST /api/events/_publish` (cross-process) |
| `domains.py` | `GET /api/domains`, `POST /api/domains/{id}/promote` |
| `clusters.py` | CRUD, match, tree, stats, templates, recluster, reassign, repair. Read endpoints use `db.autoflush=False`. Legacy 301 for `/api/patterns/*`, `/api/taxonomy/*` |

Shared: `app/utils/sse.py` (`format_sse()`), `app/dependencies/rate_limit.py` (in-memory via `limits`).

## Data models (`app/models.py`)

- `Optimization` — raw/optimized prompt, scores, clustering info, domain, per-phase model IDs, multi-embedding (optimized + transformation), `phase_weights_json` snapshot
- `PromptCluster` — UUID PK, self-join `parent_id`, L2-normalized centroid (384-dim), lifecycle state (`candidate`|`active`|`mature`|`template`|`archived`|`domain`), metrics, `cluster_metadata` JSON
- `TaxonomySnapshot` — audit trail (trigger, Q metrics, operation log, tree_state)
- `MetaPattern` — reusable techniques (`embedding`, `pattern_text`, `cluster_id` FK, `global_source_count` for cross-cluster presence)
- `OptimizationPattern` — join: Optimization→PromptCluster with similarity + relationship type
- `Feedback`, `StrategyAffinity`, `RefinementBranch`, `RefinementTurn`, `GitHubToken`, `LinkedRepo`, `RepoFileIndex`, `AuditLog`

## Pipeline architecture

- **3 phases**: analyze → optimize → score. Each is an independent LLM call with fresh context. Orchestrated by `pipeline.py`
- **Streaming**: optimize/refine use `messages.stream()` to prevent HTTP timeouts (up to 128K tokens)
- **Explore**: runs when GitHub repo linked AND `enable_explore=True`. Semantic retrieval + single-shot Haiku synthesis
- **Scoring**: skippable via `enable_scoring` preference (lean mode = 2 LLM calls). A/B randomized presentation + hybrid scoring
- **Hybrid scoring**: LLM + heuristic blended with dimension weights (structure 50%, conciseness/specificity 40%, clarity 30%, faithfulness 20%). Z-score normalization when ≥10 samples. Divergence flags at >2.5pt gap. Passthrough clamped [1.0, 10.0], excluded from z-score distribution
- **Passthrough**: `prepare_optimization` assembles prompt → external LLM processes → `save_result` persists with heuristic-only scoring

## Routing internals

Process-level singleton `RoutingManager`. `resolve_route()` is a pure function (no I/O):

| Priority | Tier | Condition | Degrade path |
|----------|------|-----------|--------------|
| 1 | `passthrough` | `force_passthrough=True` | none |
| 2 | `sampling` | `force_sampling=True` + MCP caller + sampling capable | → internal → passthrough |
| 3 | `internal` | Provider detected | none |
| 4 | `sampling` | MCP caller + sampling capable (auto) | → passthrough |
| 5 | `passthrough` | Fallback | none |

REST callers never reach sampling tiers — only MCP tool invocations can route to sampling.

**Singleton pattern**: all singletons guarded by `_process_initialized` in `mcp_server.py`. Lifespan exit has **no cleanup** — singletons survive all sessions. `_clear_stale_session()` runs at process startup only.

**Disconnect signals**: `on_mcp_disconnect()` (all SSE closed → clears both `mcp_connected` + `sampling_capable`) vs `on_sampling_disconnect()` (sampling SSE closed, non-sampling remain → clears only `sampling_capable`). Both persist to `mcp_session.json` and broadcast `routing_state_changed`.

**Middleware** (`_CapabilityDetectionMiddleware`): intercepts `initialize` JSON-RPC. Dual-layer guard prevents non-sampling clients from overwriting `sampling_capable=True`: primary check on RoutingManager state + secondary on `_sampling_sse_sessions` set. Activity tracking: sampling clients refresh `last_activity`; all clients keep session file fresh.

**Disconnect checker**: 30s poll. Connected mode: check `last_activity` staleness (>60s), read `mcp_session.json` before disconnecting. Disconnected mode: poll file for reconnection.

**Thread-safety**: no `await` between `_state` read and `_update_state()` write. Safe under asyncio cooperative scheduling. See `docs/routing-architecture.md` for diagrams.

## Sampling pipeline internals

End-to-end: MCP tool call → `handle_optimize()` → `routing.resolve()` → `sampling_pipeline.run_sampling_pipeline()` → Phase 0: Explore (optional, `SamplingLLMAdapter`) → Phase 1: Analyze → Phase 2: Optimize → Phase 3: Score → Phase 4: Suggest → persist + events.

**Structured output fallback**: (1) Tool calling via `create_message(tools=..., tool_choice=required)` → parse `tool_use` block. (2) On `McpError`: inject JSON schema as text → `_parse_text_response()` (direct JSON, code block, brace-depth). (3) Analyze-only: `_build_analysis_from_text()` keyword classification.

**Free-text phases**: `OptimizationResult` and `SuggestionsOutput` skip JSON schema to preserve markdown quality. All others get explicit schema.

**Text cleaning** (`app/utils/text_cleanup.py`): `strip_meta_header()` + `split_prompt_and_changes()` (14 marker patterns). Runs before heuristic scoring. Shared by sampling, MCP save_result, REST passthrough.

**Per-phase model capture**: `result.model` from each `create_message()` persisted to DB. IDE selects model freely — no advisory hints sent.

## MCP server monkey patches

Two SDK bug fixes in `mcp_server.py`:
1. **SSE reconnection**: allows GET without session ID for fast bridge reconnection
2. **SSE deadlock**: creates transport under `_session_creation_lock`, handles request outside — prevents infinite lock hold on SSE streams

## Taxonomy engine (`services/taxonomy/`)

Process singleton (`get_engine()`/`set_engine()`). Three paths: **hot** (per-optimization embed + adaptive cosine nearest-node), **warm** (periodic HDBSCAN + speculative lifecycle mutations gated by Q_system non-regression + domain discovery + reconciliation + zombie cleanup), **cold** (full refit + UMAP 3D + OKLab coloring + Haiku labeling + domain-link restoration + member_count reconciliation).

**Merge threshold**: adaptive `0.55 + 0.04 * log2(1 + member_count)` — grows with cluster size to prevent centroid-drift mega-clusters. Task_type mismatch penalty (-0.05) for cross-type merges. Used by hot, warm, and cold paths.

**Bookkeeping invariants**: `cluster.member_count == COUNT(optimizations WHERE cluster_id = cluster.id)`. Hot path decrements old cluster on reassignment. Merge zeros loser. Retire increments target. Cold path reconciles from Optimization rows.

**Cold path safety**: Domain nodes excluded from HDBSCAN. Self-referencing parent_ids prevented. Post-HDBSCAN domain-link restoration ensures all active clusters parent to their domain node.

**Quality**: 5-dimension Q_system (coherence, separation, coverage, DBCV, stability) with adaptive weights. Domain floor: coherence ≥0.3.

**Domain discovery**: `_propose_domains()` from coherent "general" sub-populations (≥3 members, ≥0.3 coherence, ≥60% consistent `domain_raw`). Five guardrails: color pinning, retire exemption, merge gate, coherence floor, split→candidates. Ceiling at 30.

**Multi-embedding**: hot path embeds `optimized_prompt` + computes transformation vector (`L2_norm(embed(optimized) - embed(raw))`). Score-weighted centroids: `max(0.1, score / 10.0)`. `TransformationIndex` for technique-space search. Composite fusion (`fusion.py`): blends topic + transformation + output + pattern signals with per-phase adaptive weights via `resolve_fused_embedding()`. Cross-cluster injection: patterns with `global_source_count >= 3` injected across topic boundaries.

**Modules**: `engine.py`, `clustering.py`, `lifecycle.py`, `quality.py`, `projection.py`, `coloring.py`, `labeling.py`, `snapshot.py`, `sparkline.py`, `family_ops.py`, `matching.py`, `embedding_index.py`, `transformation_index.py`, `fusion.py`, `warm_phases.py`, `warm_path.py`, `cold_path.py`.

## Testing

```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
```

- `_state()` / `_ctx()` helpers build `RoutingState`/`RoutingContext` in routing tests
- `manager` fixture creates `RoutingManager` with `tmp_path`
- Event assertions use `asyncio.Queue` subscribed to `EventBus._subscribers`
- Disconnect checker tests use real `asyncio.sleep` with short intervals
