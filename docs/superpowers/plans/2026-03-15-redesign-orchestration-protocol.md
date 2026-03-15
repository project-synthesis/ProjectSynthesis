# Project Synthesis Redesign — Orchestration Protocol

> **This document governs execution of the full redesign.** It is the inter-plan communication protocol, handoff specification, and progress tracker for all implementation phases.

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md`

---

## Phase Map

```
Phase 0: Archive & Scaffold
    │   Archive v2, create project skeleton, Alembic setup
    │   Exit: clean project structure, empty DB, all tests discoverable
    ▼
Phase 1a: Provider Layer + Pipeline Core
    │   Providers, prompt loader, strategy loader, pipeline orchestrator,
    │   pipeline contracts, core prompt templates
    │   Exit: POST /api/optimize returns a real optimized prompt
    ▼
Phase 1b: Supporting Services + Full Coverage
    │   Context resolver, adaptation tracker, heuristic scorer,
    │   optimization/feedback services, trace logger, remaining routers
    │   Exit: working API (curl-testable), 90%+ backend coverage
    ▼
Phase 2: GitHub Integration + MCP Server
    │   OAuth, explore, embedding/indexing, repo index service,
    │   3 MCP tools, standalone MCP server
    │   Exit: codebase-aware optimization, MCP tools callable
    ▼
Phase 3: Frontend
    │   VS Code workbench shell, all stores, editor/inspector/navigator,
    │   command palette, diff view, strategy picker, SSE consumption
    │   Exit: full web UI connected to backend, all features usable in browser
    ▼
Phase 4: Conversational Refinement
    │   Refinement service, timeline UI, suggestion generation,
    │   branching/rollback, parts-based SSE streaming
    │   Exit: refinement loop fully functional end-to-end
    ▼
Phase 5: Deployment & Polish
    │   Docker single-container build, init.sh, CLAUDE.md, AGENTS.md,
    │   graceful shutdown, trace log rotation, final integration tests
    │   Exit: deployable application, all docs complete
```

---

## Inter-Phase Communication Protocol

Each phase produces a **handoff artifact** (`handoff-phase-N.json`) that the next phase's agent reads before starting. This is the structured context transfer — analogous to the builder system's `agent_scratchpad.json`.

### Handoff Artifact Schema

```json
{
  "phase": 1,
  "status": "completed",
  "timestamp": "2026-03-15T12:00:00Z",
  "summary": "Core backend pipeline operational. All 9 core services implemented.",

  "files_created": [
    "backend/app/services/pipeline.py",
    "backend/app/services/prompt_loader.py"
  ],
  "files_modified": [],

  "entry_conditions_met": [
    "Project skeleton exists",
    "Alembic migrations run",
    "Virtual environment configured"
  ],

  "exit_conditions": {
    "all_passed": true,
    "tests_total": 87,
    "tests_passed": 87,
    "coverage_percent": 92,
    "verification_commands": [
      {"cmd": "cd backend && pytest", "result": "PASSED"},
      {"cmd": "curl http://localhost:8000/api/health", "result": "{\"status\": \"healthy\"}"}
    ]
  },

  "warnings": [
    "embedding_service.py loads model on first call (~5s cold start)"
  ],

  "next_phase_context": {
    "critical_interfaces": [
      "pipeline.run_pipeline() is the main entry point — takes raw_prompt, returns PipelineResult",
      "provider.complete_parsed(model, system_prompt, output_format) is the LLM call abstraction"
    ],
    "env_vars_required": [
      "ANTHROPIC_API_KEY (optional — CLI provider auto-detected first)"
    ],
    "known_limitations": [
      "GitHub integration not yet connected — codebase_context always None",
      "MCP server not started — only REST API available"
    ],
    "alembic_revision": "001_initial"
  }
}
```

### Handoff Location

All handoff artifacts stored in: `docs/superpowers/plans/handoffs/`

```
docs/superpowers/plans/handoffs/
├── handoff-phase-0.json
├── handoff-phase-1a.json
├── handoff-phase-1b.json
├── handoff-phase-2.json
├── handoff-phase-3.json
├── handoff-phase-4.json
└── handoff-phase-5.json
```

---

## Phase Entry/Exit Conditions

### Phase 0: Archive & Scaffold

**Entry conditions:** None (first phase)

**Exit conditions:**
- [ ] Current v2 source archived to `archive/v2/`
- [ ] `archive/` added to `.gitignore`
- [ ] `backend/` directory created with: `app/`, `app/services/`, `app/routers/`, `app/providers/`, `app/schemas/`, `tests/`
- [ ] `frontend/` directory created with SvelteKit skeleton
- [ ] `prompts/` directory created with all template files (content can be placeholder)
- [ ] `data/` directory created with `data/traces/` subdirectory
- [ ] Alembic initialized with initial migration (all tables from spec Sections 6 and 13: optimizations, feedbacks, strategy_affinities, github_tokens, linked_repos, repo_file_index, repo_index_meta, refinement_turns, refinement_branches)
- [ ] `backend/.venv` created with all dependencies
- [ ] `pytest` discovers test directory (0 tests, 0 errors)
- [ ] `handoff-phase-0.json` written

### Phase 1a: Provider Layer + Pipeline Core

**Entry conditions:** Phase 0 handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] All 4 providers implemented (base, claude_cli, anthropic_api, detector)
- [ ] Provider `complete_parsed()` uses `thinking: {type: adaptive}` with correct effort per model
- [ ] Haiku calls use `thinking: {type: disabled}` or omit the parameter
- [ ] All subagent outputs use `output_format` with Pydantic models for guaranteed schema compliance
- [ ] System prompts use `cache_control` for prompt caching on the API provider path
- [ ] `prompt_loader.py` implemented — loads templates, substitutes variables, validates against manifest
- [ ] `strategy_loader.py` implemented — discovers strategy files, provides list
- [ ] `pipeline.py` implemented — orchestrates analyzer → optimizer → scorer
- [ ] All phase handoff contracts (Section 12) implemented as Pydantic models with `extra="forbid"`
- [ ] Scorer receives prompts as neutral "Prompt A" / "Prompt B" with randomized assignment
- [ ] `POST /api/optimize` returns a real optimized prompt via CLI or API provider
- [ ] `GET /api/optimize/{id}` returns full result for completed optimizations (SSE reconnection support)
- [ ] SSE events stream in correct order (optimization_start → status → prompt_preview → score_card → optimization_complete)
- [ ] All prompt templates in `prompts/` have real content (agent-guidance, analyze, optimize, scoring, adaptation, strategies, manifest, README)
- [ ] `handoff-phase-1a.json` written

### Phase 1b: Supporting Services + Full Coverage

**Entry conditions:** Phase 1a handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] `context_resolver.py` enforces per-source character caps (MAX_RAW_PROMPT_CHARS, MAX_GUIDANCE_CHARS, MAX_CODEBASE_CONTEXT_CHARS, MAX_ADAPTATION_CHARS)
- [ ] Priority-based truncation verified (reverse priority order)
- [ ] Prompt length minimum (20 chars) and maximum (MAX_RAW_PROMPT_CHARS) rejection verified
- [ ] External content wrapped in `<untrusted-context>` delimiters with injection hardening
- [ ] `trace_logger.py` writes per-phase JSONL entries to `data/traces/`, readable by trace_id
- [ ] `optimization_service.py` — CRUD, sort/filter, score distribution tracking
- [ ] `feedback_service.py` — CRUD, aggregation, synchronous adaptation tracker update
- [ ] `adaptation_tracker.py` — affinity tracking, seed data, degenerate pattern detection
- [ ] `heuristic_scorer.py` — bias correction, embedding similarity, structural analysis
- [ ] Pipeline checks faithfulness score post-scoring; result flagged with warning if < 6.0
- [ ] Score clustering detection (10 optimizations early, 50 full)
- [ ] `POST /api/feedback` persists and updates adaptation tracker
- [ ] `GET /api/history` returns sorted/filtered results
- [ ] `GET /api/health` returns healthy status with pipeline metrics (score_health, avg_duration_ms)
- [ ] All 8 routers implemented (GitHub routers return 501 "not yet implemented")
- [ ] In-memory rate limiting on optimize (10/min), refine (10/min), feedback (30/min), default (60/min)
- [ ] Backend test coverage ≥ 90%
- [ ] `handoff-phase-1b.json` written (also referenced as handoff-phase-1 for Phase 2 entry)

### Phase 2: GitHub Integration + MCP Server

**Entry conditions:** Phase 1b handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] GitHub OAuth flow works (login → callback → token stored)
- [ ] Repo linking triggers background index build
- [ ] `codebase_explorer.py` produces real codebase context for linked repos
- [ ] Explore context injected into optimization pipeline
- [ ] MCP server starts on port 8001
- [ ] `synthesis_optimize` callable via MCP client
- [ ] `synthesis_prepare_optimization` + `synthesis_save_result` passthrough flow works
- [ ] Explore phase respects Haiku 4.5 budget — total input < 200K tokens (verified with test using 40+ files)
- [ ] Intent drift gate uses embedding cosine similarity; warning added if < 0.5
- [ ] All GitHub/MCP tests pass
- [ ] `handoff-phase-2.json` written

### Phase 3: Frontend

**Entry conditions:** Phase 2 handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] SvelteKit dev server starts on port 5199
- [ ] VS Code workbench layout renders (activity bar, navigator, editor, inspector, status bar)
- [ ] Prompt editor accepts input, strategy picker works
- [ ] Forge button triggers optimization, progress indicator shows, result displays
- [ ] Diff view shows original vs optimized
- [ ] Inspector shows 5-dimension scores with deltas
- [ ] History navigator shows past optimizations, sortable/filterable
- [ ] GitHub navigator shows linked repo browser
- [ ] Command palette opens on Ctrl+K
- [ ] Feedback thumbs up/down works (state embedded in `forge.svelte.ts`)
- [ ] SSE reconnection: dropped connection triggers trace_id polling fallback
- [ ] Frontend error states: backend unreachable banner, optimization failure message, no-provider message, GitHub auth failure, rate limit message
- [ ] All frontend tests pass
- [ ] `handoff-phase-3.json` written

### Phase 4: Conversational Refinement

**Entry conditions:** Phase 3 handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] `POST /api/refine` runs full pipeline pass with refinement templates
- [ ] Refinement timeline renders in editor groups
- [ ] Turn cards show scores, deltas, expandable diffs
- [ ] 3 suggestions generated per turn, clickable
- [ ] Rollback creates a fork, branch switcher works
- [ ] Score sparkline shows progression
- [ ] SSE parts stream correctly (status → prompt → scores → suggestions)
- [ ] All refinement tests pass (backend + frontend)
- [ ] `handoff-phase-4.json` written

### Phase 5: Deployment & Polish

**Entry conditions:** Phase 4 handoff artifact exists and `all_passed: true`

**Exit conditions:**
- [ ] `./init.sh` starts all 3 services
- [ ] `docker compose up --build` produces working single-container deployment
- [ ] `CLAUDE.md` written with full operational guide
- [ ] `AGENTS.md` written with MCP passthrough protocol
- [ ] Graceful shutdown handles SIGTERM correctly
- [ ] Trace log rotation works
- [ ] Final end-to-end integration test passes (optimize → refine → feedback → history)
- [ ] `handoff-phase-5.json` written with final status

---

## Agent Session Protocol

Each phase is executed by one or more agent sessions. Each session follows this protocol:

### Session Start
1. Read the phase's plan file (`plans/2026-03-15-redesign-phase-N-*.md`)
2. Read the previous phase's handoff artifact (`handoffs/handoff-phase-(N-1).json`)
3. Verify entry conditions are met
4. Begin executing tasks in order

### During Execution
- Mark each step checkbox as completed: `- [ ]` → `- [x]`
- After each task (group of steps), run verification commands
- If a test fails, debug and fix before proceeding
- If blocked, document the blocker in the plan file and stop

### Session End
1. Run ALL verification commands for the phase
2. Generate the handoff artifact (`handoff-phase-N.json`)
3. Commit all changes with a descriptive message
4. Report final status

### Error Protocol
- **Test failure:** Fix in place, do not skip. Document the fix in the plan as an addendum.
- **Dependency missing:** Check if it should have been created in a previous phase. If so: (1) create the missing dependency inline in the current phase, (2) add a warning to the current handoff artifact: "Created [file] that should have been part of Phase N", (3) re-run the previous phase's verification commands to ensure nothing broke, (4) do NOT modify the previous phase's handoff artifact or plan file.
- **Spec ambiguity:** Check the spec. If still ambiguous, make a reasonable choice and document it in the handoff artifact's `warnings` field.

---

## Progress Tracking

Overall progress is tracked in this file. Update after each phase completes:

| Phase | Plan File | Status | Tests | Coverage |
|-------|-----------|--------|-------|----------|
| 0 | `2026-03-15-redesign-phase-0-archive-scaffold.md` | Complete | 0 | — |
| 1a | `2026-03-15-redesign-phase-1a-pipeline-core.md` | Complete | 68 | 93% |
| 1b | `2026-03-15-redesign-phase-1b-supporting-services.md` | Pending | — | — |
| 2 | `2026-03-15-redesign-phase-2-github-mcp.md` | Pending | — | — |
| 3 | `2026-03-15-redesign-phase-3-frontend.md` | Pending | — | — |
| 4 | `2026-03-15-redesign-phase-4-refinement.md` | Pending | — | — |
| 5 | `2026-03-15-redesign-phase-5-deployment.md` | Pending | — | — |

---

## File Inventory (All Phases)

Master list of every file that will be created across all phases. Each file listed with its phase of creation and responsibility.

### Backend (`backend/`)

| File | Phase | Responsibility |
|------|-------|---------------|
| `app/main.py` | 0 | FastAPI app, ASGI setup, lifespan |
| `app/config.py` | 0 | Pydantic settings, env var loading |
| `app/_version.py` | 0 | Single version source |
| `app/dependencies/rate_limit.py` | 1 | In-memory rate limiting dependency |
| `app/schemas/pipeline_contracts.py` | 1 | All Pydantic contracts (Section 12) |
| `app/schemas/mcp_models.py` | 2 | MCP tool input/output models |
| `app/services/pipeline.py` | 1 | Pipeline orchestrator |
| `app/services/prompt_loader.py` | 1 | Template loading + variable substitution |
| `app/services/context_resolver.py` | 1 | Unified context assembly |
| `app/services/optimization_service.py` | 1 | Optimization CRUD |
| `app/services/feedback_service.py` | 1 | Feedback CRUD |
| `app/services/adaptation_tracker.py` | 1 | Strategy affinity tracking |
| `app/services/heuristic_scorer.py` | 1 | Passthrough bias correction |
| `app/services/strategy_loader.py` | 1 | Strategy file discovery |
| `app/services/trace_logger.py` | 1b | Per-phase JSONL trace writing, trace reading by trace_id, daily rotation |
| `app/services/codebase_explorer.py` | 2 | Codebase-aware explore (ported) |
| `app/services/embedding_service.py` | 2 | Sentence-transformers (ported) |
| `app/services/repo_index_service.py` | 2 | Background indexing (ported) |
| `app/services/github_service.py` | 2 | Token encryption (ported) |
| `app/services/github_client.py` | 2 | GitHub API calls (ported) |
| `app/services/refinement_service.py` | 4 | Refinement sessions + suggestions |
| `app/providers/base.py` | 1 | Abstract LLMProvider |
| `app/providers/claude_cli.py` | 1 | CLI subprocess provider |
| `app/providers/anthropic_api.py` | 1 | Direct API provider |
| `app/providers/detector.py` | 1 | Auto-detection |
| `app/routers/optimize.py` | 1 | Optimization endpoint |
| `app/routers/history.py` | 1 | History endpoint |
| `app/routers/feedback.py` | 1 | Feedback endpoint |
| `app/routers/providers.py` | 1 | Provider info + API key |
| `app/routers/health.py` | 1 | Health check |
| `app/routers/settings.py` | 1 | Read-only settings endpoint (all config is env-var-only; settings UI reads current state, no write path) |
| `app/routers/github_auth.py` | 2 | OAuth flow |
| `app/routers/github_repos.py` | 2 | Repo management |
| `app/routers/refinement.py` | 4 | Refinement endpoint |
| `app/mcp_server.py` | 2 | Standalone MCP server |
| `alembic/` | 0 | Migration directory |
| `alembic/versions/001_initial.py` | 0 | Initial schema migration |

### Prompts (`prompts/`)

| File | Phase | Responsibility |
|------|-------|---------------|
| `agent-guidance.md` | 1 | Orchestrator system prompt |
| `analyze.md` | 1 | Analyzer template |
| `optimize.md` | 1 | Optimizer template |
| `scoring.md` | 1 | Scorer system prompt |
| `explore.md` | 2 | Explore synthesis template |
| `adaptation.md` | 1 | Adaptation state formatter |
| `refine.md` | 4 | Refinement optimizer template |
| `suggest.md` | 4 | Suggestion generator template |
| `passthrough.md` | 2 | MCP passthrough combined template |
| `strategies/chain-of-thought.md` | 1 | Strategy content |
| `strategies/few-shot.md` | 1 | Strategy content |
| `strategies/role-playing.md` | 1 | Strategy content |
| `strategies/structured-output.md` | 1 | Strategy content |
| `strategies/meta-prompting.md` | 1 | Strategy content |
| `strategies/auto.md` | 1 | Auto-selection strategy |
| `manifest.json` | 1 | Variable validation manifest |
| `README.md` | 1 | Template documentation |

### Frontend (`frontend/src/`)

| File | Phase | Responsibility |
|------|-------|---------------|
| `routes/+layout.svelte` | 3 | Workbench shell |
| `routes/+page.svelte` | 3 | Main page |
| `lib/api/client.ts` | 3 | API client |
| `lib/stores/forge.svelte.ts` | 3 | Optimization state |
| `lib/stores/editor.svelte.ts` | 3 | Tab management |
| `lib/stores/github.svelte.ts` | 3 | GitHub state |
| `lib/stores/refinement.svelte.ts` | 4 | Refinement state |
| `lib/components/layout/ActivityBar.svelte` | 3 | Activity switcher |
| `lib/components/layout/Navigator.svelte` | 3 | Sidebar |
| `lib/components/layout/EditorGroups.svelte` | 3 | Multi-tab editor |
| `lib/components/layout/Inspector.svelte` | 3 | Right panel |
| `lib/components/layout/StatusBar.svelte` | 3 | Bottom strip |
| `lib/components/editor/PromptEdit.svelte` | 3 | Prompt textarea |
| `lib/components/editor/ForgeArtifact.svelte` | 3 | Result viewer |
| `lib/components/shared/DiffView.svelte` | 3 | Side-by-side diff |
| `lib/components/shared/CommandPalette.svelte` | 3 | Ctrl+K fuzzy finder |
| `lib/components/shared/ProviderBadge.svelte` | 3 | Provider indicator |
| `lib/components/shared/ScoreCard.svelte` | 3 | 5-dimension scores for inspector (protocol-originated, inferred from spec inspector description) |
| `lib/components/refinement/RefinementTimeline.svelte` | 4 | Turn card list |
| `lib/components/refinement/RefinementTurnCard.svelte` | 4 | Single turn |
| `lib/components/refinement/PartRenderer.svelte` | 4 | Part type switch |
| `lib/components/refinement/SuggestionChips.svelte` | 4 | Clickable suggestions |
| `lib/components/refinement/BranchSwitcher.svelte` | 4 | Branch navigation |
| `lib/components/refinement/ScoreSparkline.svelte` | 4 | Score progression |
| `lib/components/refinement/RefinementInput.svelte` | 4 | Text input |
| `lib/components/refinement/ScoreCardPart.svelte` | 4 | Score card within turn card |
| `lib/components/refinement/PromptPreviewPart.svelte` | 4 | Collapsed prompt within turn card |
| `lib/components/refinement/DiffViewPart.svelte` | 4 | Diff within turn card (wraps shared DiffView) |
| `lib/components/refinement/StatusPart.svelte` | 4 | Progress indicator within turn card |
| `lib/components/refinement/VersionMarker.svelte` | 4 | Branch/rollback badge |

### Tests (`backend/tests/`)

| File | Phase | Tests for |
|------|-------|-----------|
| `test_prompt_loader.py` | 1 | Template loading, substitution |
| `test_pipeline.py` | 1 | Full pipeline flow (mocked provider) |
| `test_context_resolver.py` | 1 | Context assembly, truncation |
| `test_contracts.py` | 1 | Pydantic contract validation |
| `test_optimization_service.py` | 1 | CRUD, sort/filter |
| `test_feedback_service.py` | 1 | Feedback CRUD, aggregation |
| `test_adaptation_tracker.py` | 1 | Affinity updates, degenerate detection |
| `test_heuristic_scorer.py` | 1 | Bias correction, heuristics |
| `test_strategy_loader.py` | 1 | Strategy discovery |
| `test_providers.py` | 1 | Provider detection, CLI/API |
| `test_routers.py` | 1 | All router endpoints |
| `test_score_calibration.py` | 1b | Clustering detection, distribution |
| `test_trace_logger.py` | 1b | Trace writing, reading, rotation |
| `test_prompt_caching.py` | 1a | Cache hit verification on API provider |
| `test_codebase_explorer.py` | 2 | Explore flow, staleness |
| `test_embedding_service.py` | 2 | Embed, cosine search |
| `test_repo_index_service.py` | 2 | Index build, query |
| `test_github.py` | 2 | OAuth, token encryption |
| `test_mcp_tools.py` | 2 | All 3 MCP tools |
| `test_refinement_service.py` | 4 | Version CRUD, branching |
| `test_refinement_pipeline.py` | 4 | Full refine flow |

### Deployment & Docs

| File | Phase | Responsibility |
|------|-------|---------------|
| `init.sh` | 5 | Service management script |
| `Dockerfile` | 5 | Single-container build |
| `docker-compose.yml` | 5 | Compose config |
| `CLAUDE.md` | 5 | Claude Code guidance |
| `AGENTS.md` | 5 | Universal agent guidance |
| `CHANGELOG.md` | 5 | Release notes |

---

## Versioning

Plan files are immutable once a phase begins execution. If a plan needs modification during execution:
1. Document the change as an addendum at the bottom of the plan file
2. Note the reason in the handoff artifact's `warnings` field
3. Do NOT rewrite completed steps

Spec file is immutable during implementation: `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md`
