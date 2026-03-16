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

## MCP passthrough protocol

For agents that want to use their own LLM to optimize prompts:

### Step 1: Prepare
Call `synthesis_prepare_optimization` with the raw prompt. Returns an assembled optimization prompt with context, strategy, and scoring rubric.

### Step 2: Process
Your LLM processes the assembled prompt, producing an optimized version with self-rated scores.

### Step 3: Save
Call `synthesis_save_result` with the optimized prompt and scores. The server applies bias correction and persists the result.

### Full pipeline (alternative)
Call `synthesis_optimize` to run the entire pipeline server-side using the configured LLM provider.

## Prompt templates

All prompts are in `prompts/`. Edit any file and changes take effect immediately (hot-reload).

**Do:**
- Edit template content directly
- Add new strategies in `prompts/strategies/`
- Follow `{{variable}}` syntax for dynamic content

**Don't:**
- Hardcode prompts in application code
- Bypass the template system
- Remove required variables (see `prompts/manifest.json`)

### Template inventory

| Template | Purpose |
|----------|---------|
| `analyze.md` | Classify prompt intent and detect weaknesses |
| `optimize.md` | Rewrite prompt using selected strategy |
| `scoring.md` | Independent 5-dimension scoring rubric |
| `refine.md` | Iterative refinement pass |
| `suggest.md` | Generate improvement suggestions |
| `explore.md` | Codebase exploration synthesis |
| `passthrough.md` | Combined template for MCP passthrough |
| `strategies/*.md` | 6 optimization strategy files |

## Architecture

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy async, SQLite
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4
- **Pipeline**: Orchestrator + 3 subagent phases (analyze -> optimize -> score)
- **Providers**: Claude CLI (Max subscribers) or Anthropic API (auto-detected at startup)
- **MCP server**: Standalone on port 8001 (`http://127.0.0.1:8001/mcp`)

## Key files

| File | Purpose |
|------|---------|
| `backend/app/services/pipeline.py` | Pipeline orchestrator |
| `backend/app/services/prompt_loader.py` | Template loading + variable substitution |
| `backend/app/services/strategy_loader.py` | Strategy file discovery |
| `backend/app/services/context_resolver.py` | Context caps + injection hardening |
| `backend/app/services/heuristic_scorer.py` | Passthrough bias correction |
| `backend/app/services/trace_logger.py` | Per-phase JSONL traces |
| `backend/app/config.py` | All configuration |
| `backend/app/providers/detector.py` | LLM provider auto-detection |
| `prompts/manifest.json` | Template variable specs |
| `init.sh` | Service management |

## Layer rules

Services follow a strict dependency direction:

```
routers/ -> services/ -> models/
```

Services must never import from routers. All GitHub token operations go through `github_service.py`.

## Development

### Run tests
```bash
cd backend && source .venv/bin/activate && pytest --cov=app -v
```

### Restart backend
```bash
pkill -f "uvicorn app.main" && cd backend && source .venv/bin/activate && \
  nohup python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000 --reload \
  > ../data/backend.log 2>&1 &
```

### Docker
```bash
docker compose up --build -d
```

## Design constraints

- **Theme**: industrial cyberpunk — dark backgrounds, 1px neon contours, no rounded corners, no shadows
- **Pagination**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`
- **Error format**: structured `{code, message}` responses
- **Versioning**: single source of truth in `backend/app/_version.py`
