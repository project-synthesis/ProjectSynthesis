# CLAUDE.md — Project Synthesis

Guidance for Claude Code when working in this repository. **Keep all CLAUDE.md files under 200 lines.** See `backend/CLAUDE.md` and `frontend/CLAUDE.md` for service-specific details.

## Versioning

**Single source of truth:** `/version.json` → `scripts/sync-version.sh` propagates to `backend/app/_version.py`, `frontend/package.json`. Frontend reads version via `$lib/version.ts` (JSON import). Health endpoint serves it at `/api/health`.

**Semver:** `MAJOR.MINOR.PATCH[-prerelease]`

| Bump | When | Example |
|------|------|---------|
| `MAJOR` | Breaking API/schema changes, incompatible migrations | 0.x → 1.0.0 |
| `MINOR` | New features, new endpoints, new MCP tools | 0.1.0 → 0.2.0 |
| `PATCH` | Bug fixes, performance, docs, dependency updates | 0.1.0 → 0.1.1 |
| `-dev` suffix | Unreleased work on main | 0.2.0-dev |

**Release workflow:**
```bash
./scripts/release.sh              # release current version (strips -dev)
./scripts/release.sh 0.4.0        # release a specific version
./scripts/release.sh --dry-run    # preview without changes
```
The script handles: version sync → commit → tag → push → GitHub Release (with changelog body) → dev bump. Requires `gh` CLI authenticated.

**Manual alternative:**
1. Edit `version.json` (remove `-dev` or bump)
2. Run `./scripts/sync-version.sh`
3. Move `docs/CHANGELOG.md` items from `## Unreleased` to `## vX.Y.Z — YYYY-MM-DD`
4. Commit: `release: vX.Y.Z`
5. Tag: `git tag vX.Y.Z && git push origin main --tags`
6. Create GitHub Release: `gh release create vX.Y.Z --notes-file <changelog> --latest`
7. Bump to next dev: edit `version.json` to next version with `-dev`, run sync, commit `chore: bump to X.Y.Z-dev`

**Changelog convention:** Every user-visible change gets a line in `docs/CHANGELOG.md` under `## Unreleased`. Categories: `Added`, `Changed`, `Fixed`, `Removed`. Write in past tense, start with a verb.

## Services and ports

| Service | Port | Entry point |
|---|---|---|
| FastAPI backend | 8000 | `backend/app/main.py` |
| SvelteKit frontend | 5199 | `frontend/src/` |
| MCP server (standalone) | 8001 | `backend/app/mcp_server.py` |

```bash
./init.sh              # start all three services
./init.sh stop         # graceful stop (process group kill)
./init.sh restart      # stop + start
./init.sh reload-mcp   # restart MCP server only (faster, requires /mcp reconnect)
./init.sh status       # show running/stopped with PIDs
./init.sh logs         # tail all service logs
./init.sh setup-vscode # install VS Code bridge extension for sampling
./init.sh update [tag] # auto-update to latest release (or specific tag)
```

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`
PIDs: `data/pids/backend.pid`, `data/pids/mcp.pid`, `data/pids/frontend.pid`

## Architecture overview

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async + aiosqlite, SQLite (`data/synthesis.db`). See `backend/CLAUDE.md`
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4. See `frontend/CLAUDE.md`
- **Pipeline**: 3 phases (analyze → optimize → score), each an independent LLM call. Models configurable per phase via preferences. **Hybrid Phase Routing** (v0.4.2): fast phases (analyze, score, suggest) run on the internal provider while optimize routes through the MCP `SamplingProvider` when the caller is sampling-capable — avoids the 5-round-trip penalty of the prior sampling-only path. Provider threaded through enrichment + tool handlers at every call site
- **Routing**: 5-tier priority chain — force_passthrough > force_sampling > internal > auto_sampling > passthrough. REST callers are excluded from sampling tiers; only MCP tool invocations can reach sampling. See `backend/CLAUDE.md` for internals, `docs/routing-architecture.md` for diagrams
- **Scoring**: hybrid LLM + heuristic with z-score normalization and divergence detection. Dimension weights v3: faithfulness 0.26, clarity/specificity 0.22, structure/conciseness 0.15
- **Providers**: Claude CLI or Anthropic API, auto-detected once at startup, stored on `app.state.routing`
- **Classification**: `semantic_upgrade_general()` gate catches LLM returning "general" when strong keywords present. Heuristic analyzer: 6-layer pipeline with compound keywords (A1), technical verb+noun disambiguation (A2), domain signal auto-enrichment from taxonomy (A3), confidence-gated Haiku LLM fallback (A4 — gates `_LLM_CLASSIFICATION_CONFIDENCE_GATE=0.40` + `_LLM_CLASSIFICATION_MARGIN_GATE=0.10`, v0.4.7). **Signal source tag** (`bootstrap` vs `dynamic`) driven by `_TASK_TYPE_EXTRACTED` set in `task_type_classifier`; warm Phase 4.75 + startup pass `extracted_task_types=set(tt_signals.keys())` so the MCP cache-only load reports `bootstrap` honestly. Single-word defaults preserved through `set_task_type_signals()` via module-level `_STATIC_SINGLE_SIGNALS` snapshot (B6). **Technical-signals rescue** (B2): `has_technical_nouns(first_sentence)` upgrades any task type to `code_aware` when a linked repo is present — vocab covers async/concurrency (`asyncio`, `coroutine`, `mutex`, `semaphore`, `deadlock`, `savepoint`); first-sentence tokenizer splits interior `.`/`-`/`/` (so `asyncio.gather`, `cache-aware`, `backend/app/...` decompose); `_looks_like_identifier` detects snake_case + PascalCase-with-structural-marker (PascalCase requires `.`/`-`/`/` in same token to avoid prose-brand false positives `JavaScript`/`GitHub`/`McDonalds`). **C6 selective inline-backtick handling** (v0.4.7): `_looks_like_code_reference` unwraps backticks containing `/`, `_`, or source extensions (real code refs); strips backticks otherwise. **Task-type structural rescue** (v0.4.5): `rescue_task_type_via_structural_evidence()` overrides `creative`/`writing` → `coding` when first sentence has snake_case identifiers, PascalCase+separator, or technical nouns; scope-guarded (analysis/data NOT rescued, they legitimately co-occur with code identifiers). **B5+ task-type lock** (v0.4.7, in `pipeline_phases.resolve_post_analyze_state`): when the prompt's first word is a writing lead verb (`write/draft/compose/author/summarize/describe/document/outline/narrate`), the heuristic also said `writing`, and the LLM said `coding`, prefer the lead-verb signal. The `write` verb additionally requires a prose-output cue (`changelog/section/style/release/page/reference/...`) to avoid false positives on `Write a function that...`. **Post-LLM domain reconciliation** (v0.4.5, in `pipeline_phases.resolve_post_analyze_state`): runs BEFORE `domain_resolver.resolve()` so the resolver sees canonical form. Two transforms: `_normalize_llm_domain()` rewrites hyphen-style sub-domains (`backend-observability`) → colon syntax (`backend: observability`) when prefix matches a registered primary domain; `enrich_domain_qualifier()` then layers organic Haiku-generated qualifiers onto bare LLM primaries. `find_best_qualifier()` tiebreaker prefers qualifier whose name appears in text on hit-count ties. **Single-source-of-truth canonicalizer**: `normalize_sub_domain_label()` in `text_cleanup.py` (kebab-case, max 30 chars, underscores→hyphens, collapse separators, word-boundary truncation) used by both vocab generation and sub-domain creation. **Analyze effort ceiling**: `ANALYZE_EFFORT_CEILING='high'` clamps `max`/`xhigh` on analyze-only (optimize/score unaffected). **Negation-aware weakness detection**: `_is_negated()` + `_compute_structural_density()` in `weakness_detector`. `DomainSignalLoader` singleton for dynamic domain keyword signals. **TaskTypeTelemetry** model records heuristic vs LLM classifications for drift analysis + A4 tuning (migration `2f3b0645e24d`)
- **Taxonomy**: evolutionary hierarchical clustering (`services/taxonomy/`). **Hierarchy**: project → domain → sub-domain → cluster → optimizations. **Templates**: mature clusters crossing usage+score thresholds fork an immutable `PromptTemplate` row via `TemplateService.fork_from_cluster()` — the source cluster stays at `state='mature'`. Warm Phase 0 reconciles `template_count` + auto-retires templates whose source degrades or is archived; Phase 4 recomputes `preferred_strategy` for clusters with `template_count > 0`. Hot/warm/cold paths. **Warm path architecture**: two execution groups — lifecycle (Phases 0–4, dirty-cluster-gated) and maintenance (Phases 5–6, cadence-gated via `MAINTENANCE_CYCLE_INTERVAL=6` + `_maintenance_pending` retry flag). **Phase 4.5** (global patterns: promote/validate/retire) runs each sub-step in its own `begin_nested()` SAVEPOINT so a transient failure in one step doesn't poison the maintenance transaction. **Phase 4.95** runs `_propose_sub_domains(vocab_only=True)` in an isolated DB session so stale vocabulary reads don't short-circuit Phase 5. **Multi-project** (ADR-005): project nodes (`state="project"`) created on GitHub repo link via `project_service.py`. Two-tier hot-path assignment: in-project first, cross-project fallback with +0.15 boosted threshold. Per-project Q metrics in speculative phases. **Dirty-set tracking**: `dict[str, str|None]` (cluster→project). **Adaptive scheduler**: linear regression boundary, all-dirty vs per-project budget allocation with proportional quotas (min floor=3) and per-project starvation guard. **Global patterns**: `GlobalPattern` model promoted from cross-project MetaPattern siblings (Phase 4.5, every 10th cycle). **EmbeddingIndex**: dual-backend (numpy default, HNSW at ≥1000 clusters, fallback to numpy on HNSW failure) with stable label mapping + tombstones. Domain discovery + sub-domain discovery. Adaptive merge threshold `0.55 + 0.04 * log2(1 + member_count)`. Multi-embedding blended clustering: 0.55 raw + 0.20 optimized + 0.15 transformation + 0.10 qualifier. `QualifierIndex` tracks per-cluster qualifier centroids. Score-weighted centroids. 5-signal composite fusion (`PhaseWeights` with adaptive weight learning, **T1.1 Bayesian shrinkage** v0.4.7 — `SCORE_ADAPTATION_PRIOR_KAPPA=8.0` + `SCORE_ADAPTATION_MIN_SAMPLES=2`, replaces the prior min-10 hard gate). **Spectral clustering** primary split (HDBSCAN fallback). **Sub-domain discovery**: fully organic — enriched Haiku-generated vocabulary from cluster labels (`generated_qualifiers` in domain node metadata), cached in `DomainSignalLoader`. Vocabulary generation receives per-cluster centroid similarity matrix (`_VOCAB_SIM_HIGH=0.7`/`_VOCAB_SIM_LOW=0.3`, `None` for unknown cells) + intent labels + domain_raw qualifier distribution as structured context (`ClusterVocabContext` dataclass) **plus (v0.4.7) `domain_signal_keywords` (top TF-IDF orphans) + `existing_vocab_groups`** so Haiku absorbs latent themes the cascade is recording exclusively via source 3 and prefers existing group names when geometry hasn't shifted. Post-generation quality metric emitted via `vocab_generated_enriched` event; `avg_vocab_quality` exposed in health endpoint. Three-source qualifier cascade: domain_raw > intent_label > TF-IDF signal_keywords, extracted into shared pure primitive `compute_qualifier_cascade()` in `sub_domain_readiness.py` consumed by both `_propose_sub_domains()` and `/api/domains/readiness` (no drift by construction). **TF-IDF extraction (v0.4.7)**: `_extract_domain_keywords()` branches on `cluster.state == "domain"` and aggregates `raw_prompt` text across descendant active/mature clusters (domain nodes never own opts directly); output min-max normalized so the top keyword weighs `1.0` and the cascade's `>= 0.5` admit gate becomes meaningful. Closes the previous structural silence where `tf_idf` source recorded 0 hits across every domain. Adaptive consistency threshold `max(0.40, 0.60 - 0.004 * members)`. **Readiness telemetry**: `GET /api/domains/readiness` + `GET /api/domains/{id}/readiness` expose cascade state, stability guards, and emergence gap with 30s TTL cache keyed by `(domain_id, member_count)`. `readiness_history.py` JSONL snapshot writer (30-day retention, hourly buckets) backs `GET /api/domains/{id}/readiness/history`. Tier-crossing detector (2-cycle hysteresis + per-domain cooldown) publishes `domain_readiness_changed` SSE events, gated by the `domain_readiness_notifications` preference. **Sub-domain lifecycle**: re-evaluated every Phase 5 cycle; dissolved at consistency < 0.25 (hysteresis gap vs 0.40–0.60 creation); dissolution reparents clusters, merges meta-patterns (prompts never lost); `dissolved_this_cycle` set prevents flip-flop. `DomainResolver.remove_label()` clears dissolved labels. **Domain lifecycle**: `_reevaluate_domains()` evaluates top-level domains every Phase 5 cycle. 5 guards: "general" permanent, sub-domain anchor (bottom-up), age ≥48h, member ceiling ≤5, consistency <15% (Source 1 only, 45-point hysteresis). Shared `_dissolve_node()` handles both domain and sub-domain dissolution. Seed domains have no special protection (ADR-006). Split children as `state="candidate"`, evaluated by Phase 0.5. **Cluster dissolution**: small incoherent clusters dissolved. Phase 0 reconciles member counts, prunes stale archived clusters, re-parents clusters whose domain doesn't match parent node (sub-domain children preserved). `mega_cluster_prevention` gate
- **Batch seeding**: explore-driven pipeline generates diverse prompts from project descriptions, optimizes in parallel with full enrichment aligned with `pipeline.py` — resolved tier threaded end-to-end (no hardcoded `"internal"`), `ContextEnrichmentService.enrich()` drives pattern injection + strategy intelligence + codebase context + B0 repo relevance gate + B1/B2 divergence alerts + enrichment-profile selection, `ClassificationAgreement` recorded per prompt, bulk persists with quality gate (score ≥ 5.0) emitting `optimization_created` per inserted row with `source="batch_seed"`, generates suggestions per seed, assigns taxonomy. 5 default agents in `prompts/seed-agents/` (hot-reloaded). `synthesis_seed` MCP tool + `POST /api/seed` REST. `SeedModal` in topology view. 9 observability events (`seed_*`) + per-prompt `optimization_created`. Concurrency: CLI=10, API=5, sampling=2
- **Observability**: `TaxonomyEventLogger` dual-writes structured decision events to JSONL (`data/taxonomy_events/`) + in-memory ring buffer (500). 21+ instrumentation points across hot/warm/cold/seed paths including `global_pattern/promoted|demoted|re_promoted|retired`. `taxonomy_activity` SSE events. Frontend `ActivityPanel` with path/op/error filters and expandable context. **Taxonomy Observatory** (v0.4.4): pinned `OBSERVATORY` workbench tab mounting `TaxonomyObservatory.svelte`, a three-panel shell — `DomainLifecycleTimeline` (reverse-chrono SSE-live + JSONL backfill via `/api/clusters/activity/history?since/until`), `DomainReadinessAggregate` (composes existing `DomainStabilityMeter` + `SubDomainEmergenceList` per domain), `PatternDensityHeatmap` (read-only data grid backed by `/api/taxonomy/pattern-density`). Period selector (`24h | 7d | 30d`) lives in Timeline filter-bar (NOT shell header — Readiness is current-state) and drives both Timeline + Heatmap via `observatoryStore`. Click on Aggregate card dispatches `domain:select` CustomEvent → `clustersStore.selectCluster()` for navigator/topology focus
- **Live Pattern Intelligence** (ADR-007 Tier 1, v0.4.4): persistent `ContextPanel.svelte` sidebar mounted by `EditorGroups.svelte` alongside the prompt editor — replaces the legacy single-banner `PatternSuggestion`. Reads `clustersStore.suggestion` and renders matched cluster identity (label + similarity % + match_level + chromatic domain dot), meta-patterns checkboxes, and a neon-purple-bordered GLOBAL section for cross-cluster patterns. APPLY commits multi-pattern selection to `forgeStore.appliedPatternIds`. Backend `POST /api/clusters/match` response gains additive keys `match_level: 'family'|'cluster'` + `cross_cluster_patterns: MetaPatternItem[]` (no schema migration). Mount-gated to `editorStore.activeTab?.type === 'prompt'`, hidden during synthesis. < 1400 px viewport defaults to a 28 px rail (user-toggle persists in `localStorage['synthesis:context_panel_open']`)
- **Layer rules**: `routers/` → `services/` → `models/` only. Services must never import from routers
- **Model IDs**: centralized in `config.py` (`MODEL_SONNET/OPUS/HAIKU`). Use `PreferencesService.resolve_model()`, never hardcode
- **Env vars**: `ANTHROPIC_API_KEY` (optional), `SECRET_KEY` (auto-generated). GitHub auth uses Device Flow with hardcoded App client ID — no env vars needed

## Prompt templates

All prompts live in `prompts/`. `{{variable}}` syntax. Hot-reloaded on each call. Validated at startup against `manifest.json`.

| Template | Purpose |
|----------|---------|
| `agent-guidance.md` | Orchestrator system prompt (static) |
| `analyze.md` | Analyzer: classify + detect weaknesses |
| `optimize.md` | Optimizer: rewrite using strategy |
| `scoring.md` | Scorer: independent 5-dimension evaluation (static) |
| `refine.md` | Refinement optimizer (replaces optimize.md during refinement) |
| `suggest.md` | Suggestion generator (3 per turn) |
| `explore.md` | Codebase exploration synthesis (Haiku) |
| `explore-guidance.md` | Codebase analysis guidance for structured context extraction (static) |
| `passthrough.md` | MCP passthrough combined template |
| `extract_patterns.md` | Meta-pattern extraction from completed optimizations (Haiku) |
| `seed.md` | Batch seed agent prompt generation template |
| `strategies/*.md` | Strategy files with YAML frontmatter. Fully adaptive — add/remove files to change available strategies |
| `seed-agents/*.md` | Seed agent definitions with YAML frontmatter (name, task_types, phase_context, prompts_per_run, enabled) |

Variable reference: `prompts/manifest.json`

## MCP server

14 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`). All use `structured_output=True`. Tool handlers in `backend/app/tools/*.py`; `mcp_server.py` is a thin registration layer. See `AGENTS.md` for detailed tool usage guidance.

**Adding a tool:**
1. Define Pydantic output model in `schemas/mcp_models.py`
2. Create handler in `backend/app/tools/<name>.py` with `handle_<name>()` async function
3. Re-export from `backend/app/tools/__init__.py`
4. Add `@mcp.tool(structured_output=True)` wrapper in `mcp_server.py`
5. Use `synthesis_` prefix. Access routing via `get_routing()` from `tools/_shared.py`. Raise `ValueError` for user errors

## Common tasks

```bash
./init.sh restart                                              # restart all services
cd backend && source .venv/bin/activate && pytest --cov=app -v # backend tests
cd frontend && npm run test                                    # frontend tests
cd frontend && npm run dev                                     # frontend dev server standalone
docker compose up --build -d                                   # docker deployment
```

## Roadmap protocol

`docs/ROADMAP.md` tracks improvements requiring breaking changes (schema migrations, multi-file refactors, breaking API changes). Non-breaking improvements should be implemented immediately — never deferred to the roadmap.

## IDE integration

### VS Code (sampling pipeline)
Two-layer integration: `.vscode/mcp.json` enables native MCP discovery (VS Code 1.99+), and the bridge extension (`VSGithub/mcp-copilot-extension/`) adds full sampling pipeline support. Run `./init.sh setup-vscode` to install — detects VS Code across standard, snap, flatpak, Insiders, Codium, and custom paths. The `chat.mcp.serverSampling` setting in `.vscode/settings.json` pre-approves sampling so no user consent dialog appears.

### `.mcp.json` (Claude Code)
Auto-loads the Project Synthesis MCP server (`http://127.0.0.1:8001/mcp`) when this directory is open in Claude Code. Verify with `./init.sh status`.

### Hooks (`.claude/hooks/`)

| Hook | Purpose | Timeout |
|------|---------|---------|
| `pre-pr-checks.sh` | Unified PreToolUse gate — runs Ruff + svelte-check + template-guard before real `git push` / `gh pr create` invocations. Uses shlex-based tokenisation to avoid false positives on commands that merely *mention* the gate keywords (e.g. `grep -r "git push" docs/`). Fast path exits in <5 ms for non-matching commands. | 180s |
| `pre-pr-template-guard.sh` | Grep guard: blocks residual `state='template'` literals in source files. Called by `pre-pr-checks.sh`; CWD-safe. | 30s |

Exit codes: `0` = allow, `2` = block (fix errors first).

### Subagents (`.claude/agents/`)
- **`code-reviewer.md`** — Architecture compliance, brand guidelines, and consistency review.

## Key invariants

- **Pagination**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`
- **GitHub tokens**: Fernet-encrypted at rest. `github_service.encrypt_token`/`decrypt_token` are the only entry points
- **API key**: encrypted at rest in `data/.api_credentials`. Provider hot-reloads when key is set via `GET/PATCH/DELETE /api/provider/api-key`
- **Secrets**: `SECRET_KEY` auto-generated on first startup, persisted to `data/.app_secrets` (0o600)
- **Event bus**: `event_bus.py` publishes to SSE subscribers. Types: `optimization_created/analyzed/updated/deleted/failed`, `optimizations_migrated`, `feedback_submitted`, `refinement_turn`, `strategy_changed`, `preferences_changed`, `taxonomy_changed`, `taxonomy_activity`, `routing_state_changed`, `domain_created`, `domain_readiness_changed`, `domain_candidate_detected`, `domain_archival_suggested`, `domain_ceiling_reached`, `domain_signals_refreshed`, `repo_unlinked`, `seed_batch_progress`, `agent_changed`, `index_phase_changed`, `update_available`, `update_complete`. Cross-process `taxonomy_changed` is bridged into the resident engine's dirty_set via `_apply_cross_process_dirty_marks()` before firing the warm-path timer, so MCP/CLI deletes reconcile on the next cycle instead of waiting for maintenance cadence. MCP server notifies via HTTP POST to `/api/events/_publish`
- **Database hardening**: `@event.listens_for(engine.sync_engine, "connect")` applies `journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`, `cache_size=-64000`, `foreign_keys=ON` to every pool checkout (per-connection PRAGMAs reset on reconnect, hence the event hook). `pool_pre_ping=True` + `pool_recycle=3600` validate stale connections after `./init.sh restart`
- **Recurring GC**: `run_recurring_gc()` in `gc.py` sweeps expired `GitHubToken` rows (24h grace for in-flight refreshes, legacy non-expiring tokens preserved) + orphan `LinkedRepo` rows hourly via `_recurring_gc_task` scheduled in `lifespan`. Startup-only GC functions (`_gc_failed_optimizations`, `_gc_archived_zero_member_clusters`, `_gc_orphan_meta_patterns`) run once per boot
- **Preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase, pipeline toggles, default strategy, per-phase effort
- **Trace logging**: per-phase JSONL to `data/traces/`, daily rotation, configurable retention
- **Taxonomy observability**: per-decision JSONL to `data/taxonomy_events/`, 30-day rotation. `TaxonomyEventLogger` singleton initialized in lifespan in **both** backend and MCP processes. Ring buffer (500) + JSONL dual-write. MCP process events forwarded to backend ring buffer via cross-process HTTP POST. Score events include `intent_label`. `get_event_logger()` raises `RuntimeError` if uninitialized — all call sites wrapped in `try/except RuntimeError: pass`
- **Multi-project isolation** (ADR-005, hybrid 2026-04-19): `PromptCluster` with `state="project"` as **sibling root** (`parent_id IS NULL`) — never a tree parent. Clusters parent to domain nodes; project ownership lives in `PromptCluster.dominant_project_id` (denormalized view-filter FK, populated by warm Phase 0 + cold path). `LinkedRepo.project_node_id` FK. `Optimization.project_id` denormalized. `EXCLUDED_STRUCTURAL_STATES` frozenset (domain, archived, project) used in ALL state exclusion queries. `_cluster_project_cache` on engine for fast lookup. `EmbeddingIndex` vectors tagged with `project_id` via parallel `_project_ids` array. Full rationale: `docs/hybrid-taxonomy-plan.md` + ADR-005 Amendment 2026-04-19
- **Global pattern tier** (ADR-005): `GlobalPattern` table (500 cap). Promoted from MetaPattern siblings that cross BOTH gates — `GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS` (5) cross-cluster breadth AND `GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS` (2) distinct source projects. Patterns that live inside a single project stay project-scoped until a sibling emerges elsewhere. Injected with 1.3x boost. Validated every 10th warm cycle with 30-min gate. Demotion at avg_score < 5.0, re-promotion at >= 6.0 (1.0-point hysteresis). `OptimizationPattern.global_pattern_id` FK for provenance
- **Adaptive scheduling** (ADR-005): `AdaptiveScheduler` with rolling 10-cycle window. `_compute_boundary()` linear regression. `decide_mode()` returns all-dirty or round-robin (per-project budget allocation). Budget mode: proportional quota per project with `_MIN_QUOTA=3` floor, per-project starvation counters (`_STARVATION_LIMIT=3`), starved projects steal quota from largest donor. All projects with dirty clusters served every cycle. `snapshot()` exposes `project_budgets`, `starvation_counters`. Measurement uses total dirty count (pre-scoping)
- **Split merge protection**: `cluster_metadata["merge_protected_until"]` — naive UTC timestamp, 60-minute window. `split_failures` persisted outside speculative transactions to prevent Groundhog Day loops. `mega_cluster_prevention` gate blocks merges that would re-form an oversized cluster (Groundhog Day variant 3). Growth-based cooldown reset at 25% member increase. Split children are `state="candidate"` — evaluated by Phase 0.5 before promotion to active
- **Context enrichment**: unified `ContextEnrichmentService.enrich()` for all routing tiers. Auto-selected profiles: `code_aware` (all layers), `knowledge_work` (skip codebase), `cold_start` (skip strategy+patterns). Heuristic analysis runs for ALL tiers. 4 active context sources: `heuristic_analysis`, `codebase_context` (task-gated curated retrieval + explore synthesis + workspace fallback), `strategy_intelligence` (merged perf signals + adaptation feedback, C1 domain-relaxed fallback), `applied_patterns` (full `auto_inject_patterns()` for passthrough/refine, deferred to pipeline for internal/sampling). Few-shot examples in ALL tiers including passthrough, **MMR-diversified (v0.4.7) at `FEW_SHOT_MMR_LAMBDA=0.6`** so the budget covers diverse modes rather than clustering near the prompt embedding. `applied_pattern_texts` stored in `enrichment_meta` for post-optimization UI attribution. Repo relevance gate (B0): `compute_repo_relevance()` returns `(cosine, info_dict)` with single floor `REPO_RELEVANCE_FLOOR=0.15` against a project-anchored synthesis — anchor prepends `Project: {repo_full_name}\n` and appends up to 500 indexed file paths as `Components:` (stride-sampled at 100 for MiniLM's 512-token window). `extract_domain_vocab(synthesis, file_paths)` retained for `domain_matches` info only (UI attribution, never gating). Reason codes: `above_floor` / `below_floor`. Same-stack-different-project separation comes from the project name in the anchor, not vocabulary overlap. Prompt-context divergence detection (B1+B2): `detect_divergences()` flags tech stack conflicts, `divergence_alerts` property renders 4-category intent classification instructions for optimizer. **B5 full-prompt rescue + B5+ codebase trim** (v0.4.7): when first-sentence rescue (B2) misses but the prompt body has technical content AND task is writing/creative, scan the full prompt with `has_technical_nouns` and upgrade to `code_aware` (sets `full_prompt_technical_rescue=True`); `_writing_about_code` (heuristic OR lead-verb writing AND repo linked AND tech signals) caps codebase context at `WRITING_CODE_CONTEXT_CAP_CHARS=15000` instead of the default 80K to prevent CHANGELOG-style prompts from hallucinating against related-but-wrong code. Tracks `rescue_path` (`B2_first_sentence`/`B5_full_prompt`) + `trigger` (`heuristic_task_type`/`lead_verb`). Classification agreement tracking (E1): `ClassificationAgreement` singleton records heuristic vs LLM comparison + strategy intelligence hit rate. Zero request-time LLM calls except A4 confidence-gated Haiku fallback (~15-20% of prompts, gated by `enable_llm_classification_fallback` preference)
- **Workspace intelligence**: auto-detects project type from manifests + deep scanning (README, entry points, architecture docs). Collapsed into codebase context as fallback when explore synthesis absent
- **GitHub auth**: Device Flow OAuth with hardcoded GitHub App client ID — zero-config, no secret, no callback URL. Expiring tokens: `refresh_token` + `expires_at` stored, auto-refreshed by `_get_session_token()`. `github_me` validates token live with GitHub API; cleans up stale token + linked repo on revocation. Frontend: unified `connectionState` getter (7 states: `disconnected | expired | authenticated | linked | indexing | error | ready`), `reconnect()` clears `linkedRepo` before re-auth; `phaseLabel` + `indexErrorText` surface live index progress and errors via the `index_phase_changed` SSE event. Fallback callback flow retained for server-side use (`GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` env vars)
- **Refinement**: each turn is a fresh pipeline invocation with context enrichment (codebase context + strategy intelligence), not multi-turn accumulation. Rollback creates a branch fork. 3 suggestions per turn
- **Feedback adaptation**: strategy affinity counter. Degenerate pattern detection (>90% same rating over 10+ feedbacks). Phase weight adaptation: 3-layer bootstrap — (1) task-type bias initialization (`_TASK_TYPE_WEIGHT_BIAS` × 7 types), (2) warm-path score-correlated adaptation (`compute_score_correlated_target()` — uses `improvement_score` for z-score weighting, EMA alpha=0.05) at global and per-cluster level, (3) decay toward cluster learned profiles (rate=0.01). Dual few-shot retrieval: input-similar (cosine >= 0.50) + output-similar (cosine >= 0.40), merged by `max(sim) * score`. Three closed feedback loops: weight learning, few-shot quality, cluster lifecycle
