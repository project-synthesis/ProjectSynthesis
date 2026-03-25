# Security Hardening — Full DevSecOps Audit & Remediation

**Date:** 2026-03-25
**Status:** Approved
**Scope:** Full sweep — all severity levels across auth, API surface, infrastructure, and dependencies

## Context

Project Synthesis is currently deployed as a local-only single-developer tool (option A), but the codebase must be future-proofed for internet-facing deployment with authentication (option C). Security controls are environment-gated so local dev has zero friction while production deployments enforce all hardening measures.

A parallel three-agent audit identified ~30 distinct findings across authentication, API surface, infrastructure, and dependencies. Findings were deduplicated and consolidated into 7 work areas, delivered across 3 PRs grouped by risk profile.

## Deployment Model

| Environment | Behavior |
|-------------|----------|
| Local dev | `DEVELOPMENT_MODE=true`, no MCP auth token, `http://localhost` CORS, cookies without `secure` flag |
| Production | `DEVELOPMENT_MODE=false`, `MCP_AUTH_TOKEN` set, explicit `FRONTEND_URL`, `secure=true` cookies, HSTS enabled |

Detection is automatic — no manual toggle beyond setting the appropriate env vars.

## PR Structure

| PR | Work Areas | Risk Profile |
|----|-----------|-------------|
| PR 1: Critical Path | W1 (Cookies), W2 (MCP Auth), W3 (Input Validation) | Exploitable findings |
| PR 2: Infrastructure | W4 (CORS/Headers), W5 (Crypto), W6 (Deployment) | Defense-in-depth |
| PR 3: Hygiene | W7 (Dependencies/Observability) | Operational quality |

## W1: Cookie & Session Security

**Files:** `backend/app/routers/github_auth.py`

### W1a. SameSite attribute
Add `samesite="lax"` to both `set_cookie` calls (state cookie at line 56, session cookie at line 130). `"lax"` chosen over `"strict"` because the OAuth callback is a cross-site redirect from GitHub — `"strict"` would strip the state cookie before validation, breaking the flow.

### W1b. Secure flag (environment-gated)
Add helper `_is_secure() -> bool` that returns `True` when `settings.FRONTEND_URL` starts with `https://`. Pass `secure=_is_secure()` to both cookies. Local dev auto-detects `http://` and skips the flag.

### W1c. Session lifetime reduction
Reduce `max_age` from `86400 * 30` (30 days) to `86400 * 14` (14 days). Halves the hijack window while avoiding excessive re-auth friction.

### W1d. Cookie path scoping
Add `path="/api"` to the session cookie. It's only consumed by API routes — no reason for frontend static asset requests to transmit it.

**Constraint:** This scopes the cookie to `/api/*` paths only. All current session-consuming endpoints (`/api/github/auth/me`, `/api/github/repos/*`) are within this path. Any future endpoint outside `/api/` that needs session state must either be moved under `/api/` or this path must be widened.

### W1e. CSRF protection rationale
SameSite=Lax is the sole CSRF protection. This is adequate because all state-mutating endpoints use POST/PATCH/DELETE methods, and SameSite=Lax only sends cookies on same-site requests or top-level GET navigations. No state-mutating GET endpoints exist. A dedicated CSRF token mechanism is not needed under this threat model but should be reconsidered if GET-with-side-effects endpoints are ever added.

## W2: MCP Server Authentication

**Files:** `backend/app/mcp_server.py` (inline middleware, colocated with existing `_CapabilityDetectionMiddleware`), `backend/app/config.py`, `nginx/nginx.conf`
**ADR:** `docs/adr/ADR-001-mcp-authentication.md`

### W2a. Environment-gated bearer token middleware
New ASGI middleware applied to the MCP server:

- Read `MCP_AUTH_TOKEN` from env (via `config.py`, `Optional[str] = None`)
- **Not set** -> middleware is a no-op (local dev unchanged)
- **Set** -> every HTTP request must carry `Authorization: Bearer <token>`. Returns `401 Unauthorized` with generic message on mismatch. SSE transport fallback: accepts `?token=<value>` query param for clients that cannot set headers on EventSource connections.

**SSE token query param caveat:** Tokens in query strings appear in nginx access logs, browser history, and proxy logs. Production deployments must configure nginx `log_format` to strip or mask the `token` query parameter. The `?token=` fallback is gated behind a separate config flag `MCP_ALLOW_QUERY_TOKEN` (default `True` for dev convenience, set `False` in production when all clients support header-based auth).

The middleware is defined inline in `mcp_server.py`, colocated with the existing `_CapabilityDetectionMiddleware`. This follows the current project convention — no new `middleware/` directory needed.

### W2b. Nginx proxy guard
The `/mcp` location block in nginx adds a second layer:

- If auth header present, proxy to port 8001
- If not present and request is not from localhost, return 403
- Defense-in-depth — even if middleware has a bug, nginx blocks external unauthenticated access

### W2c. ADR-001
Documents the decision, alternatives considered (session-forwarding rejected for breaking headless IDE clients, localhost-only rejected for limiting future remote integrations like Notion/Figma, OAuth deferred as heavier feature pass), and consequences.

### Decision rationale (MCP auth)
MCP's ecosystem is evolving toward remote Streamable HTTP transport. Cloud-hosted IDE plugins, Notion/Figma integrations, and multi-machine workflows need network access to the MCP server. Localhost binding would block all of these. Bearer token auth at the HTTP transport layer is invisible to the MCP protocol layer — any compliant client can pass it via standard config.

## W3: Input Validation & Error Handling

**Files:** `backend/app/routers/preferences.py`, `backend/app/routers/strategies.py`, `backend/app/routers/history.py`, `backend/app/routers/github_repos.py`, `backend/app/routers/optimize.py`, `backend/app/routers/github_auth.py`, `backend/app/utils/sse.py`, `backend/app/dependencies/rate_limit.py`, `backend/app/schemas/` (feedback schema)

### W3a. Preferences PATCH schema
Replace untyped `dict` body with strict Pydantic model `PreferencesUpdate`. `model_config = ConfigDict(extra="forbid")` rejects unknown keys at the schema level. Allowed fields (all `Optional`):

- `default_strategy: str` — strategy name
- `enable_explore: bool` — codebase exploration toggle
- `enable_scoring: bool` — scorer phase toggle
- `enable_adaptation: bool` — adaptation tracker toggle
- `optimizer_effort: Literal["low", "medium", "high", "max"]`
- `analyzer_effort: Literal["low", "medium", "high", "max"]`
- `scorer_effort: Literal["low", "medium", "high", "max"]`
- `analyzer_model: str` — model preference for analyzer phase
- `optimizer_model: str` — model preference for optimizer phase
- `scorer_model: str` — model preference for scorer phase
- `force_passthrough: bool` — force passthrough routing
- `force_sampling: bool` — force sampling routing

Schema derived from current `PreferencesService` accepted keys. Any new preference must be added to this schema first.

### W3b. Feedback comment length limit
Add `max_length=2000` to the `comment` field in `FeedbackRequest`. Generous for real feedback, blocks multi-MB payloads that could bloat the database.

### W3c. Strategy file size cap
Add check before write: `if len(body.content) > 50_000: raise HTTPException(413, "Strategy file exceeds 50KB limit")`. Matches the existing 50KB read-side cap in `strategy_loader.py`.

### W3d. Sort column validation
Add a `Depends` validator on the `sort_by` query parameter in `history.py` that reads from the canonical `VALID_SORT_COLUMNS` frozenset in `optimization_service.py`. This avoids creating a second source of truth (a hardcoded regex in the router would drift from the service's column set). Implementation:

```python
def validate_sort_by(sort_by: str = Query("created_at")) -> str:
    if sort_by not in VALID_SORT_COLUMNS:
        raise HTTPException(422, f"Invalid sort column. Must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}")
    return sort_by
```

Fail-fast at router level while remaining DRY with the service layer.

### W3e. Repo name format validation
Validate `full_name` matches `^[a-zA-Z0-9_.-]+/[a-zA-Z0-9._-]+$` before hitting GitHub API. Prevents malformed input from reaching external services.

### W3f. Error message sanitization
Replace `detail=str(exc)` patterns with generic messages across all routers. Log full exceptions server-side at `logger.error`. Complete target list:

- `preferences.py` line 28: `detail=str(exc)` → generic 422 message
- `optimize.py` line 210: remove trace_id echo from 404 detail
- `github_auth.py` line 99: log OAuth error descriptions, return generic "Authentication failed" to client
- `feedback.py` line 64: `detail=str(e)` → generic "Failed to submit feedback"
- `refinement.py` line 212: `detail=str(exc)` → generic "Rollback failed"
- `strategies.py` line 75: `detail=f"Failed to read strategy file: {exc}"` → generic "Strategy not found"
- `strategies.py` line 117: `detail="Failed to write strategy file: %s" % exc` → generic "Failed to save strategy"

### W3g. SSE serialization safety
Wrap `json.dumps()` in `format_sse()` with try/except. Log serialization failures, return safe `{"event": "error", "error": "Internal error"}` payload. Prevents stack trace leakage.

### W3h. X-Forwarded-For parsing
Strip whitespace from comma-split segments. Validate extracted IP via `ipaddress.ip_address()`. Fall back to direct `request.client.host` if parsing fails.

## W4: CORS & HTTP Headers

**Files:** `backend/app/main.py`, `backend/app/config.py`, `nginx/nginx.conf`

### W4a. Whitelist CORS methods and headers
Replace wildcards:

- `allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]`
- `allow_headers=["Content-Type", "Authorization", "Cache-Control"]`

### W4b. Environment-gated localhost origin
New config field: `DEVELOPMENT_MODE` (bool, default `False`). Named explicitly to avoid collision with FastAPI/Starlette's `debug` parameter (which controls exception tracebacks — a separate concern). CORS origins always include `FRONTEND_URL`. Only include `http://localhost:5199` when `DEVELOPMENT_MODE=True`. Local dev `.env` sets `DEVELOPMENT_MODE=true`.

**Important:** `DEVELOPMENT_MODE` must NOT be wired to FastAPI's `debug=True`. They are independent settings. `DEVELOPMENT_MODE` gates: localhost CORS origin, cookie secure flag fallback, and any other dev-convenience behavior. FastAPI debug mode should always be `False` in this application.

### W4c. HSTS in nginx
Uncomment existing HSTS header. Gate with `if ($scheme = https)` so it only fires when TLS is active. `max-age=31536000; includeSubDomains`.

### W4d. CSP tightening
- Change `connect-src 'self' ws:` to `connect-src 'self' ws: wss:`
- `frame-ancestors` is already set to `'none'` in the current nginx config. Keep `'none'` — the application has no legitimate iframe embedding use case. Do not weaken to `'self'`.

## W5: Cryptography & Secrets

**Files:** `backend/app/services/github_service.py`, `backend/app/routers/providers.py`, new `backend/app/utils/crypto.py`
**ADR:** `docs/adr/ADR-002-encryption-key-derivation.md`

### W5a. PBKDF2 key derivation
Replace `hashlib.sha256(secret.encode()).digest()` with:

```python
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

kdf = PBKDF2HMAC(
    algorithm=hashes.SHA256(),
    length=32,
    salt=context_salt,  # unique per credential type
    iterations=600_000,
)
key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
```

600K iterations per current OWASP recommendation. Static salt is acceptable because the input SECRET_KEY is already high-entropy random.

### W5b. Separate encryption contexts
Shared utility `backend/app/utils/crypto.py` with `derive_fernet(secret: str, context: str) -> Fernet`. Different static salts per credential type:

- `b"synthesis-github-token-v1"` for GitHub tokens
- `b"synthesis-api-credential-v1"` for API keys

Same SECRET_KEY, distinct derived keys. Compromising one context does not reveal the other.

**Both call sites must be migrated:**
- `github_service.py` (`GitHubService.__init__`) — currently derives Fernet inline
- `providers.py` (`_read_api_key()` and `_write_api_key()`) — currently derives Fernet inline with identical SHA256 pattern

Both switch to `derive_fernet(settings.SECRET_KEY, context)`. The derived Fernet instance should be cached per (secret_key, context) pair at module level to avoid the ~200-500ms PBKDF2 latency on every call. `GitHubService` already creates a Fernet on construction — the same pattern applies to the providers router (cache in a module-level dict, invalidated only if SECRET_KEY changes).

### W5c. Legacy migration
Migration is **lazy** — triggered on first decrypt attempt per credential type, not eagerly at startup. Flow:

1. `derive_fernet(secret, context)` returns the PBKDF2-derived Fernet
2. Caller attempts decrypt → if `InvalidToken`, caller invokes `derive_fernet_legacy(secret)` (SHA256 path)
3. If legacy decrypt succeeds → re-encrypt with new Fernet and persist
4. If legacy also fails → credential is genuinely corrupt, raise error

This is handled by a helper `decrypt_with_migration(ciphertext, secret, context, persist_fn)` in `crypto.py` that encapsulates the fallback logic. `persist_fn` is a callback to write the re-encrypted value (keeps crypto.py decoupled from storage).

Both `github_service.py` and `providers.py` must use `decrypt_with_migration()` to ensure complete migration coverage.

### W5d. API key format validation
Extend existing `sk-` prefix check with `len(key) >= 40`. Catches truncated/garbage input without being brittle to format changes.

### W5e. ADR-002
Documents: why PBKDF2 over Argon2 (no extra C dependency, `cryptography` already in tree), why static salts are acceptable, why separate contexts, migration path.

## W6: Infrastructure & Deployment

**Files:** `init.sh`, `Dockerfile`, `.dockerignore`, `nginx/`

### W6a. Data directory permissions
Set `chmod 700 data/` after creation in `init.sh`. Owner-only access. Currently `0775`.

### W6b. Docker secrets hygiene
The existing `.dockerignore` already excludes `data/`, `.env`, `.env.*`, `*.pem`, `*.key`. Since `.app_secrets` and `.api_credentials` live inside `data/`, they are already covered. Net new additions: `*.p12`, `*.pfx`, `*.jks` (certificate/keystore formats). Runtime secrets via `docker-compose.yml` environment/secrets.

### W6c. init.sh process safety
The current `stop_service` function already uses PID-file-based stopping with a graceful SIGTERM-then-SIGKILL sequence (lines 306-319). The hardening is limited to the fallback path:

- Replace `pgrep -f` fallback (lines 286-289) with `pgrep -f -u "$(id -u)"` — only match processes owned by the current user
- The process group kill (`kill -9 -- -"$pid"`) in the final fallback is the only remaining aggressive path — add a comment documenting when it triggers and why it's necessary (orphaned child processes after PID file loss)

### W6d. Log rotation
Enhance size-based rotation to also rotate on service start. Add `MAX_LOG_FILES=5` with pruning of oldest beyond that count. Document that production should use logrotate or a log aggregator.

### W6e. Nginx error page
Replace branded 50x page with generic "Service unavailable" — no application name, version, or technology hints.

## W7: Dependencies & Observability

**Files:** `backend/requirements.txt`, `frontend/package.json`, `backend/app/models.py`, new `backend/app/services/audit_logger.py`, all routers
**ADR:** `docs/adr/ADR-003-dependency-pinning-strategy.md`

### W7a. Pin Python dependencies
Most packages are already pinned with `==`. The remaining unpinned packages using `>=` ranges:

- `watchfiles>=1.0.0`
- `numpy>=1.26.0`
- `scikit-learn>=1.3`
- `umap-learn>=0.5.5`
- `scipy>=1.11`

Pin these 5 to exact `==` versions from the current working environment (`pip freeze`). Add a comment header with pin date and update instructions: `# Pinned 2026-03-25. To update: pip install --upgrade <pkg> && update pin here`.

### W7b. Pin frontend dependencies
Remove `^` prefixes. Commit `package-lock.json`. CI uses `npm ci --frozen-lockfile`.

### W7c. Audit logging
New `AuditLog` model:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | UUID | PK |
| `timestamp` | DateTime | Event time |
| `action` | String | Enum: `api_key_set`, `api_key_deleted`, `github_login`, `github_logout`, `strategy_updated`, `mcp_auth_failure` |
| `actor_ip` | String | Client IP (respects trusted proxies) |
| `actor_session` | String (nullable) | Session ID if available |
| `detail` | JSON | Sanitized context (no secrets) |
| `outcome` | String | `success` or `failure` |

New `audit_logger.py` service: `async def log_event(action, request, detail, outcome)`. Instrumented on: API key CRUD, GitHub OAuth, strategy writes, MCP auth failures. Auto-prune entries older than `AUDIT_RETENTION_DAYS` (default 90) via background task.

**Migration:** New `AuditLog` table requires an Alembic migration: `alembic revision --autogenerate -m 'add audit_log table'`. Applied automatically on startup via `docker-entrypoint.sh` (existing `alembic upgrade head` call).

### W7d. Rate limit coverage expansion
Add `RateLimit` dependency to unprotected endpoints:

| Endpoint | Limit |
|----------|-------|
| `GET /api/health` | 60/minute |
| `GET /api/settings` | 60/minute |
| `GET /api/clusters/{id}` | 60/minute |
| `GET /api/strategies` | 60/minute |

### W7e. ADR-003
Documents: why exact pins, update workflow, lockfile policy, trade-off between reproducibility and freshness.

## Out of Scope (Intentional)

| Item | Reason |
|------|--------|
| OAuth-based MCP auth | Deferred to dedicated feature pass |
| PostgreSQL migration | Documented as production recommendation, not required for hardening |
| Redis-backed rate limiting | Documented for multi-server deployments |
| Full WAF/CDN layer | Deployment-specific, outside application code |
| Argon2 KDF | Extra C dependency, PBKDF2 via `cryptography` is sufficient |
| SQL injection | Mitigated by SQLAlchemy ORM parameterized queries — no raw SQL in codebase |
| CSRF tokens | SameSite=Lax sufficient for current threat model (see W1e rationale) |

## ADR Index

| ADR | Title | Status |
|-----|-------|--------|
| ADR-001 | MCP Server Authentication Strategy | Accepted |
| ADR-002 | Encryption Key Derivation | Accepted |
| ADR-003 | Dependency Pinning Strategy | Accepted |

## Testing Strategy

Each PR includes tests:

### PR 1 tests
- MCP auth middleware: token present/absent/wrong, no-op when `MCP_AUTH_TOKEN` unset, `?token=` query param fallback
- Cookie attributes: assert `samesite="lax"`, `path="/api"`, `max_age=86400*14` on session cookie; assert `secure=True` when `FRONTEND_URL` is HTTPS
- Input validation edge cases: oversized strategy (>50KB), malformed repo name, invalid sort column via `Depends` validator, feedback comment at `max_length` boundary, preferences with unknown keys rejected

### PR 2 tests
- Crypto migration end-to-end: encrypt a value with legacy SHA256 derivation, then decrypt with `decrypt_with_migration()` — verify transparent re-encryption and subsequent direct decrypt with new KDF
- Crypto context separation: verify that a value encrypted with `github-token-v1` context cannot be decrypted with `api-credential-v1` context
- CORS assertions: test both `DEVELOPMENT_MODE=True` (localhost origin included) and `DEVELOPMENT_MODE=False` (localhost origin absent). Edge case: `FRONTEND_URL` is HTTPS but `DEVELOPMENT_MODE=True`
- Error sanitization: verify that exception details do not appear in HTTP response bodies for each target in W3f

### PR 3 tests
- Audit log: write event, verify fields, run prune with `AUDIT_RETENTION_DAYS=0`, verify deletion
- Rate limit: verify 429 response on newly protected endpoints (`/api/health`, `/api/settings`, `/api/clusters/{id}`, `/api/strategies`) when limit exceeded
- Dependency pins: verify all entries in `requirements.txt` use `==` (no `>=`, `~=`, or bare versions). Verify `package-lock.json` is committed and consistent with `package.json`
