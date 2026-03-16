# Contributing to Project Synthesis

Thank you for considering contributing. This document covers getting started with local development.

## Setup

```bash
# Backend
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Frontend
cd ../frontend && npm install

# Start services
cd .. && ./init.sh start
```

## Development Workflow

1. Create a branch from `main`
2. Make your changes
3. Run backend tests: `cd backend && source .venv/bin/activate && pytest --cov=app -v`
4. Run frontend type check: `cd frontend && npx svelte-check`
5. Commit with a descriptive message
6. Open a pull request

## Code Style

- **Backend**: Python 3.12+, Ruff for linting, type hints on all public methods
- **Frontend**: SvelteKit 2, Svelte 5 runes, Tailwind CSS 4
- **Brand**: Industrial cyberpunk — no glow, no shadows, 1px borders, dark backgrounds. See `.claude/skills/brand-guidelines/`

## Architecture Rules

- `routers/` → `services/` → `models/` only. Services never import from routers.
- All prompts in `prompts/` with `{{variable}}` syntax. Never hardcode prompts in code.
- Model IDs centralized in `config.py` (`MODEL_SONNET`, `MODEL_OPUS`, `MODEL_HAIKU`).
- Provider detected once at startup. Never call `detect_provider()` in request handlers.
- All list endpoints use the pagination envelope: `{total, count, offset, items, has_more, next_offset}`.

## Adding a New Strategy

1. Create `prompts/strategies/your-strategy.md` with static content (no variables)
2. It's automatically discovered by `strategy_loader.py`
3. The analyzer will include it in `available_strategies`

## Adding a New MCP Tool

1. Add `@mcp.tool(name="synthesis_...")` in `backend/app/mcp_server.py`
2. Use the `synthesis_` prefix
3. Return a Pydantic model or raise `ValueError` for errors
