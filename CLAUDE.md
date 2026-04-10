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
1. Edit `version.json` (remove `-dev` or bump)
2. Run `./scripts/sync-version.sh`
3. Move `docs/CHANGELOG.md` items from `## Unreleased` to `## vX.Y.Z — YYYY-MM-DD`
4. Commit: `release: vX.Y.Z`
5. Tag: `git tag vX.Y.Z && git push origin main --tags`
6. Bump to next dev: edit `version.json` to next version with `-dev`, run sync, commit `chore: bump to X.Y.Z-dev`

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
./init.sh status       # show running/stopped with PIDs
./init.sh logs         # tail all service logs
./init.sh setup-vscode # install VS Code bridge extension for sampling
```

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`
PIDs: `data/pids/backend.pid`, `data/pids/mcp.pid`, `data/pids/frontend.pid`

## Architecture overview

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async + aiosqlite, SQLite (`data/synthesis.db`). See `backend/CLAUDE.md`
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4. See `frontend/CLAUDE.md`
- **Pipeline**: 3 phases (analyze → optimize → score), each an independent LLM call. Models configurable per phase via preferences
- **Routing**: 5-tier priority chain — force_passthrough > force_sampling > internal > auto_sampling > passthrough. See `backend/CLAUDE.md` for internals, `docs/routing-architecture.md` for diagrams
- **Scoring**: hybrid LLM + heuristic with z-score normalization and divergence detection
- **Providers**: Claude CLI or Anthropic API, auto-detected once at startup, stored on `app.state.routing`
- **Classification**: `semantic_upgrade_general()` gate catches LLM returning "general" when strong keywords present. Heuristic analyzer for zero-LLM passthrough classification
- **Taxonomy**: evolutionary hierarchical clustering (`services/taxonomy/`). **Hierarchy**: project → domain → cluster → optimizations. Hot/warm/cold paths. **Multi-project** (ADR-005): project nodes (`state="project"`) created on GitHub repo link via `project_service.py`. Two-tier hot-path assignment: in-project first, cross-project fallback with +0.15 boosted threshold. Per-project Q metrics in speculative phases. **Dirty-set tracking**: `dict[str, str|None]` (cluster→project). **Adaptive scheduler**: linear regression boundary, all-dirty vs round-robin mode with starvation guard. **Global patterns**: `GlobalPattern` model promoted from cross-project MetaPattern siblings (Phase 4.5, every 10th cycle). **EmbeddingIndex**: dual-backend (numpy default, HNSW at ≥1000 clusters) with stable label mapping + tombstones. Domain discovery + sub-domain discovery. Adaptive merge threshold `0.55 + 0.04 * log2(1 + member_count)`. Multi-embedding HDBSCAN: blended 0.65/0.20/0.15. Score-weighted centroids. Composite fusion. **Spectral clustering** primary split (HDBSCAN fallback). Split children as `state="candidate"`, evaluated by Phase 0.5. **Cluster dissolution**: small incoherent clusters dissolved. Phase 0 reconciles member counts, prunes stale archived clusters. `mega_cluster_prevention` gate
- **Batch seeding**: explore-driven pipeline generates diverse prompts from project descriptions, optimizes in parallel with full enrichment (pattern injection, few-shot retrieval, adaptation state, domain resolution, z-score normalization), bulk persists with quality gate (score ≥ 5.0), generates suggestions per seed, assigns taxonomy. 5 default agents in `prompts/seed-agents/` (hot-reloaded). `synthesis_seed` MCP tool + `POST /api/seed` REST. `SeedModal` in topology view. 9 observability events (`seed_*`). Concurrency: CLI=10, API=5, sampling=2
- **Observability**: `TaxonomyEventLogger` dual-writes structured decision events to JSONL (`data/taxonomy_events/`) + in-memory ring buffer (500). 21+ instrumentation points across hot/warm/cold/seed paths including `global_pattern/promoted|demoted|re_promoted|retired`. `taxonomy_activity` SSE events. Frontend `ActivityPanel` with path/op/error filters and expandable context
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
| `adaptation.md` | Adaptation state formatter |
| `passthrough.md` | MCP passthrough combined template |
| `extract_patterns.md` | Meta-pattern extraction from completed optimizations (Haiku) |
| `seed.md` | Batch seed agent prompt generation template |
| `strategies/*.md` | Strategy files with YAML frontmatter. Fully adaptive — add/remove files to change available strategies |
| `seed-agents/*.md` | Seed agent definitions with YAML frontmatter (name, task_types, phase_context, prompts_per_run, enabled) |

Variable reference: `prompts/manifest.json`

## MCP server

13 tools with `synthesis_` prefix on port 8001 (`http://127.0.0.1:8001/mcp`). All use `structured_output=True`. Tool handlers in `backend/app/tools/*.py`; `mcp_server.py` is a thin registration layer. See `AGENTS.md` for detailed tool usage guidance.

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
| `pre-pr-ruff.sh` | Python lint via Ruff on `backend/app/` and `backend/tests/` | 60s |
| `pre-pr-svelte.sh` | Svelte type check via `npx svelte-check` on `frontend/` | 120s |

Exit codes: `0` = allow, `2` = block (fix errors first).

### Subagents (`.claude/agents/`)
- **`code-reviewer.md`** — Architecture compliance, brand guidelines, and consistency review.

## Key invariants

- **Pagination**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`
- **GitHub tokens**: Fernet-encrypted at rest. `github_service.encrypt_token`/`decrypt_token` are the only entry points
- **API key**: encrypted at rest in `data/.api_credentials`. Provider hot-reloads when key is set via `GET/PATCH/DELETE /api/provider/api-key`
- **Secrets**: `SECRET_KEY` auto-generated on first startup, persisted to `data/.app_secrets` (0o600)
- **Event bus**: `event_bus.py` publishes to SSE subscribers. Types: `optimization_created/analyzed/failed`, `feedback_submitted`, `refinement_turn`, `strategy_changed`, `preferences_changed`, `taxonomy_changed`, `taxonomy_activity`, `routing_state_changed`, `domain_created`, `seed_batch_progress`, `agent_changed`, `update_available`, `update_complete`. MCP server notifies via HTTP POST to `/api/events/_publish`
- **Preferences**: file-based JSON (`data/preferences.json`), loaded as frozen snapshot per pipeline run. Model selection per phase, pipeline toggles, default strategy, per-phase effort
- **Trace logging**: per-phase JSONL to `data/traces/`, daily rotation, configurable retention
- **Taxonomy observability**: per-decision JSONL to `data/taxonomy_events/`, 30-day rotation. `TaxonomyEventLogger` singleton initialized in lifespan in **both** backend and MCP processes. Ring buffer (500) + JSONL dual-write. MCP process events forwarded to backend ring buffer via cross-process HTTP POST. Score events include `intent_label`. `get_event_logger()` raises `RuntimeError` if uninitialized — all call sites wrapped in `try/except RuntimeError: pass`
- **Multi-project isolation** (ADR-005): `PromptCluster` with `state="project"` as tree parent. `LinkedRepo.project_node_id` FK. `Optimization.project_id` denormalized. `EXCLUDED_STRUCTURAL_STATES` frozenset (domain, archived, project) used in ALL state exclusion queries. `_cluster_project_cache` on engine for fast lookup. `EmbeddingIndex` vectors tagged with `project_id` via parallel `_project_ids` array
- **Global pattern tier** (ADR-005): `GlobalPattern` table (500 cap). Promoted from MetaPattern siblings spanning 2+ projects, 5+ clusters. Injected with 1.3x boost. Validated every 10th warm cycle with 30-min gate. Demotion at avg_score < 5.0, re-promotion at >= 6.0 (1.0-point hysteresis). `OptimizationPattern.global_pattern_id` FK for provenance
- **Adaptive scheduling** (ADR-005): `AdaptiveScheduler` with rolling 10-cycle window. `_compute_boundary()` linear regression. `decide_mode()` returns all-dirty or round-robin. `_STARVATION_LIMIT=3` prevents project neglect. Measurement uses total dirty count (pre-scoping)
- **Split merge protection**: `cluster_metadata["merge_protected_until"]` — naive UTC timestamp, 60-minute window. `split_failures` persisted outside speculative transactions to prevent Groundhog Day loops. `mega_cluster_prevention` gate blocks merges that would re-form an oversized cluster (Groundhog Day variant 3). Growth-based cooldown reset at 25% member increase. Split children are `state="candidate"` — evaluated by Phase 0.5 before promotion to active
- **Context enrichment**: unified `ContextEnrichmentService.enrich()` for all routing tiers. Two-layer codebase context: cached explore synthesis (background Haiku, once per repo link/reindex) + per-prompt curated retrieval (semantic file search, 30K char cap, 5-min TTL). Zero request-time LLM calls for context. All call sites default `workspace_path` to `PROJECT_ROOT`
- **Workspace intelligence**: auto-detects project type from manifests + deep scanning (README, entry points, architecture docs)
- **GitHub auth**: Device Flow OAuth with hardcoded GitHub App client ID — zero-config, no secret, no callback URL. Fallback callback flow retained for server-side use (`GITHUB_OAUTH_CLIENT_ID`/`GITHUB_OAUTH_CLIENT_SECRET` env vars)
- **Refinement**: each turn is a fresh pipeline invocation with context enrichment (workspace guidance + adaptation state), not multi-turn accumulation. Rollback creates a branch fork. 3 suggestions per turn
- **Feedback adaptation**: strategy affinity counter. Degenerate pattern detection (>90% same rating over 10+ feedbacks). Phase weight adaptation: 3-layer bootstrap — (1) task-type bias initialization (`_TASK_TYPE_WEIGHT_BIAS` × 7 types), (2) warm-path score-correlated adaptation (`compute_score_correlated_target()` — uses `improvement_score` for z-score weighting, EMA alpha=0.05) at global and per-cluster level, (3) decay toward cluster learned profiles (rate=0.01). Dual few-shot retrieval: input-similar (cosine >= 0.50) + output-similar (cosine >= 0.40), merged by `max(sim) * score`. Three closed feedback loops: weight learning, few-shot quality, cluster lifecycle
