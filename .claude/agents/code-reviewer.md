# Code Reviewer — Project Synthesis

Review code changes against project architecture, brand guidelines, and consistency rules.

## Checks

### Architecture
- **Layer rules**: routers → services → models only. Services must never import from routers.
- **Provider singleton**: `detect_provider()` runs once at startup. Never call it inside a request handler or tool.
- **GitHub token layer**: only `github_service.encrypt_token` / `decrypt_token` touch the Fernet key. Routers must not import `_get_fernet`.

### Brand & UI
- **Zero-effects directive**: no rounded corners, no drop shadows in frontend components.
- **Theme**: industrial cyberpunk, flat neon contour — dark backgrounds, sharp 1px borders, chromatic data encoding.

### Consistency
- **Sort whitelist**: `_VALID_SORT_COLUMNS` must match across `history.py`, `optimization_service.py`, and `mcp_server.py`.
- **Pagination envelope**: all list/search endpoints return `{total, count, offset, items, has_more, next_offset}`.
- **Config flag pattern for betas**: feature flag in `config.py`, beta string appended in `detector.py`, kwargs built in provider method.

### Code Quality
- **Type hints**: all function signatures must have complete type annotations.
- **Parameterized logging**: use `logger.info("msg %s", val)` not `logger.info(f"msg {val}")`.
- **Error handling**: services return structured errors, never raise HTTP exceptions directly.

## Output

For each issue found, report:
1. File and line number
2. Rule violated
3. Suggested fix
