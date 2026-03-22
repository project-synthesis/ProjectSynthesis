<!-- GitHub Copilot workspace instructions for Project Synthesis -->

## Project Synthesis

AI-powered prompt optimization platform. Backend: FastAPI + SQLAlchemy async + SQLite. Frontend: SvelteKit 2 (Svelte 5 runes) + Tailwind CSS 4 + Three.js.

## Architecture Rules

- `routers/` → `services/` → `models/` only. Services never import from routers.
- All prompts live in `prompts/` with `{{variable}}` syntax. Never hardcode prompts.
- Model IDs centralized in `backend/app/config.py`. Use `PreferencesService.resolve_model()`.
- Provider detected once at startup via `app.state.routing`. Never call `detect_provider()` in handlers.
- All list endpoints use pagination envelope: `{total, count, offset, items, has_more, next_offset}`.

## Frontend Style

- Industrial cyberpunk: dark backgrounds (`#06060c`), 1px neon contours, no rounded corners, no shadows, no glow effects.
- Colors from `$lib/utils/colors.ts`: `scoreColor()`, `taxonomyColor()`, `qHealthColor()`, `stateColor()`.
- Svelte 5 runes: `$state`, `$derived`, `$effect`. No legacy `$:` reactive statements.

## Key Services

- `backend/app/services/pipeline.py` — 3-phase pipeline: analyze → optimize → score
- `backend/app/services/taxonomy/engine.py` — evolutionary taxonomy with hot/warm/cold paths
- `frontend/src/lib/stores/clusters.svelte.ts` — unified cluster store (not `patternsStore`)
