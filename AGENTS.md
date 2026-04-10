# AGENTS.md — Project Synthesis

Universal guidance for AI coding agents (Cursor, Copilot, Windsurf, Gemini CLI, Claude Code).

## Quick start

```bash
./init.sh              # start all services
./init.sh status       # verify running
```

- Backend: http://localhost:8000/api/health
- Frontend: http://localhost:5199
- MCP: http://localhost:8001/mcp

## MCP tools

13 tools available at `http://127.0.0.1:8001/mcp` (all use `structured_output=True`):

### Core pipeline

| Tool | Use when... |
|------|------------|
| `synthesis_optimize` | Full pipeline — analyze, optimize, score, persist. Auto-routes: internal provider → sampling → passthrough. If `status='pending_external'`, process `assembled_prompt` with your LLM, then call `synthesis_save_result` |
| `synthesis_analyze` | Quality assessment without optimization — task type, weaknesses, baseline scores, strategy recommendation |
| `synthesis_prepare_optimization` | Your own LLM should do the optimization — server assembles context + strategy + rubric into a single prompt |
| `synthesis_save_result` | After your LLM processes the prepared prompt — server applies hybrid scoring (z-score + heuristic blending) and persists |

### Workflow

| Tool | Use when... |
|------|------------|
| `synthesis_health` | Check system capabilities at session start — provider, available tiers, strategies, stats |
| `synthesis_strategies` | List available optimization strategies with descriptions before choosing one |
| `synthesis_match` | Search knowledge graph for similar prompts — returns reusable patterns to pass as `applied_pattern_ids` to `synthesis_optimize` |
| `synthesis_feedback` | Rate a completed optimization (thumbs_up/thumbs_down) to drive strategy adaptation |
| `synthesis_refine` | Iteratively improve an optimized prompt with specific instructions (requires local provider) |
| `synthesis_history` | Query past optimizations with filtering, sorting, and pagination |
| `synthesis_get_optimization` | Retrieve full details of a specific optimization by ID or trace_id |
| `synthesis_seed` | Batch-generate diverse prompts via seed agents — bootstraps the knowledge graph taxonomy |
| `synthesis_explain` | Plain-English explanation of what an optimization changed and why |

### Recommended workflow

```
synthesis_health → synthesis_match → synthesis_optimize → synthesis_feedback
```

### Passthrough protocol (prepare → process → save)

1. Call `synthesis_prepare_optimization` with the raw prompt → get assembled optimization prompt + `trace_id`
2. Your LLM processes it, producing an optimized version with self-rated scores (1.0-10.0)
3. Call `synthesis_save_result` with the `trace_id` and result → server applies hybrid scoring (z-score + heuristic blending) and persists

## Prompt templates

All prompts are in `prompts/`. Edit any file and changes take effect immediately (hot-reload).

**Do:**
- Edit template content directly
- Add new strategies in `prompts/strategies/` (auto-detected via file watcher)
- Follow `{{variable}}` syntax for dynamic content
- Include YAML frontmatter in strategy files (`tagline`, `description`)

**Don't:**
- Hardcode prompts in application code
- Bypass the template system
- Remove required variables (see `prompts/manifest.json`)

### Template inventory

| Template | Purpose |
|----------|---------|
| `analyze.md` | Classify prompt intent and detect weaknesses |
| `optimize.md` | Rewrite prompt using selected strategy |
| `scoring.md` | Independent 5-dimension hybrid scoring rubric |
| `refine.md` | Iterative refinement pass |
| `suggest.md` | Generate improvement suggestions |
| `explore.md` | Codebase exploration synthesis |
| `passthrough.md` | Combined template for MCP passthrough |
| `strategies/*.md` | Strategy files with YAML frontmatter — fully adaptive (add/remove to change available strategies) |

## Architecture

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async, SQLite
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4
- **Pipeline**: 3 phases (analyze → optimize → score), models configurable per phase via preferences
- **Scoring**: Hybrid — LLM scores blended with model-independent heuristics + z-score normalization
- **Providers**: Claude CLI (Claude Code subscription) or Anthropic API (auto-detected at startup)
- **MCP server**: Standalone on port 8001, 13 tools with structured output
- **Routing**: 5-tier priority chain — force_passthrough > force_sampling > internal > auto_sampling > passthrough

## Key files

| File | Purpose |
|------|---------|
| `backend/app/services/pipeline.py` | Pipeline orchestrator (3-phase, preferences-driven) |
| `backend/app/services/prompt_loader.py` | Template loading + `{{variable}}` substitution |
| `backend/app/services/strategy_loader.py` | Strategy discovery from disk with frontmatter parsing |
| `backend/app/services/score_blender.py` | Hybrid scoring: LLM + heuristic blending |
| `backend/app/services/heuristic_scorer.py` | Model-independent scoring heuristics + `score_prompt()` |
| `backend/app/services/preferences.py` | Persistent user preferences (`data/preferences.json`) |
| `backend/app/services/routing.py` | 5-tier routing engine (provider, sampling, passthrough) |
| `backend/app/services/context_enrichment.py` | Unified context enrichment for all tiers |
| `backend/app/services/file_watcher.py` | Real-time strategy file watching (watchfiles) |
| `backend/app/mcp_server.py` | MCP server with 13 tools |
| `backend/app/config.py` | All configuration |
| `backend/app/providers/detector.py` | LLM provider auto-detection |
| `prompts/manifest.json` | Template variable specs |
| `init.sh` | Service management |

## Layer rules

Services follow a strict dependency direction:

```
routers/ → services/ → models/
```

Services must never import from routers. All GitHub token operations go through `github_service.py`.

## Development

```bash
# Run tests (~90s, 1037 tests)
cd backend && source .venv/bin/activate && pytest --cov=app -v

# Restart services
./init.sh restart

# Docker
docker compose up --build -d
```

## Design constraints

- **Theme**: industrial cyberpunk — dark backgrounds, 1px neon contours, no rounded corners, no shadows
- **Pagination**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`
- **Strategies**: file-driven from `prompts/strategies/*.md` — no hardcoded lists
- **Models**: configurable per phase via `GET/PATCH /api/preferences`
- **Scoring**: hybrid (LLM + heuristic) with z-score normalization. Passthrough scores clamped to [1.0, 10.0]
- **Events**: real-time SSE at `/api/events` — event types drive UI reactivity
- **Domain validation**: `DomainResolver` (cached DB lookup in `domain_resolver.py`) — domains are `PromptCluster` nodes with `state="domain"`. `DomainSignalLoader` provides keyword signals from domain metadata. Invalid domains fall back to "general"
