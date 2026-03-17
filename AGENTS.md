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

4 tools available at `http://127.0.0.1:8001/mcp`:

| Tool | Use when... |
|------|------------|
| `synthesis_analyze` | You want a quality assessment without optimization — returns task type, weaknesses, baseline scores, and next steps |
| `synthesis_optimize` | You want the full pipeline — analyze, optimize, score, persist |
| `synthesis_prepare_optimization` | Your own LLM should do the optimization — server assembles context + strategy + rubric |
| `synthesis_save_result` | After your LLM processes the prepared prompt — server applies bias correction and persists |

### Passthrough protocol (prepare → process → save)

1. Call `synthesis_prepare_optimization` with the raw prompt → get assembled optimization prompt
2. Your LLM processes it, producing an optimized version with self-rated scores
3. Call `synthesis_save_result` with the result → server applies bias correction and persists

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
- **Providers**: Claude CLI (Max subscribers) or Anthropic API (auto-detected at startup)
- **MCP server**: Standalone on port 8001, 4 tools

## Key files

| File | Purpose |
|------|---------|
| `backend/app/services/pipeline.py` | Pipeline orchestrator (3-phase, preferences-driven) |
| `backend/app/services/prompt_loader.py` | Template loading + `{{variable}}` substitution |
| `backend/app/services/strategy_loader.py` | Strategy discovery from disk with frontmatter parsing |
| `backend/app/services/score_blender.py` | Hybrid scoring: LLM + heuristic blending |
| `backend/app/services/heuristic_scorer.py` | Model-independent scoring heuristics + `score_prompt()` |
| `backend/app/services/preferences.py` | Persistent user preferences (`data/preferences.json`) |
| `backend/app/services/file_watcher.py` | Real-time strategy file watching (watchfiles) |
| `backend/app/mcp_server.py` | MCP server with 4 tools |
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
# Run tests (251 tests)
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
- **Scoring**: hybrid (LLM + heuristic) with z-score normalization
- **Events**: real-time SSE at `/api/events` — 6 event types drive UI reactivity
