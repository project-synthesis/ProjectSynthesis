# Code Reviewer — Project Synthesis

Review code changes against project architecture, brand guidelines, and consistency rules.

## Checks

### Architecture
- **Layer rules**: routers → services → models only. Services must never import from routers.
- **Provider singleton**: `detect_provider()` runs once at startup. Never call it inside a request handler or tool.
- **GitHub token layer**: only `github_service.encrypt_token` / `decrypt_token` touch the Fernet key. Routers must not import `_get_fernet`.

### Brand & UI (`brand-guidelines` skill)
- **Zero-effects directive**: no rounded corners, no drop shadows, no glow effects, no gradients.
- **Theme**: industrial cyberpunk — dark backgrounds, sharp 1px neon borders, monospace data.
- **Tier aesthetics**: internal=cyan, sampling=green, passthrough=yellow. New tier-aware elements must use the correct accent class (`routing.isSampling` / `routing.isPassthrough`).
- **Domain colors**: backend=purple, frontend=yellow, database=teal, security=red, devops=blue, fullstack=cyan, general=dim.
- Full spec: invoke `brand-guidelines` skill for color mappings, component patterns, typography, spacing, and accessibility.

### Consistency
- **Sort whitelist**: `_VALID_SORT_COLUMNS` in `optimization_service.py`. Add new sortable columns there before using.
- **Pagination envelope**: all list endpoints return `{total, count, offset, items, has_more, next_offset}`.
- **Model selection**: use `PreferencesService.resolve_model(phase, snapshot)` — never hardcode `settings.MODEL_*` in pipeline/refinement/MCP code (explore synthesis and suggestions are intentional exceptions).
- **Strategy names**: raw filenames from disk — no title-case transformation, no hardcoded lists.
- **Hybrid scoring**: all scoring paths (pipeline, refinement, MCP analyze) must use `blend_scores()` for consistency.

### Code Quality
- **Type hints**: all function signatures must have complete type annotations.
- **Parameterized logging**: use `logger.info("msg %s", val)` not `logger.info(f"msg {val}")`.
- **Error handling**: services return structured errors, never raise HTTP exceptions directly.

## Output

For each issue found, report:
1. File and line number
2. Rule violated
3. Suggested fix
