# Integration Tests Design

**Date:** 2026-03-09
**Status:** Approved
**Scope:** Backend API integration tests + Playwright E2E tests

---

## Goals

- Complement the existing 246 unit tests (all mocked) with tests that exercise the full stack against a real database
- Catch integration bugs: ownership scoping, pagination envelope shape, soft-delete window enforcement, SSE pipeline events, frontend↔backend wiring
- Run in CI as a **parallel job** alongside the existing unit test job — same PR trigger, no blocking dependency

---

## Approach

### Backend: `httpx.AsyncClient` + `ASGITransport`

The FastAPI app is instantiated with two dependency overrides:
- `get_session` → yields sessions bound to a fresh per-module SQLite file (`tmp_path_factory`)
- `get_provider` → returns a `MockProvider` that returns a canned optimized prompt (no real LLM calls)

No port binding, no subprocess — fast and deterministic. `scope="module"` gives each test file its own isolated database.

### Frontend: Playwright `webServer` fixtures

`playwright.config.ts` declares two `webServer` entries:
1. FastAPI on port 8099 — started via `uvicorn`, uses `sqlite+aiosqlite:///./e2e_test.db`
2. SvelteKit production build served via `vite preview` on port 4173

A `POST /test/token` endpoint (only mounted when `ENV=test`) issues a pre-signed JWT to seed browser auth state without going through OAuth.

---

## Directory Structure

```
backend/tests/integration/
  __init__.py
  conftest.py              # engine, client, auth_headers, seeded_optimization fixtures
  test_history_api.py      # GET/DELETE/trash/restore/stats — full coverage
  test_optimize_api.py     # POST /api/optimize SSE, GET/PATCH /api/optimize/{id}
  test_auth_api.py         # GET+PATCH /auth/me, /auth/token, /auth/refresh
  test_github_api.py       # /auth/github/status, /auth/github/me
  test_providers_api.py    # /api/providers/detect, /status, /api/settings round-trip
  test_mcp_api.py          # initialize, tools/list (assert 16 tools), key tool round-trips

frontend/e2e/
  conftest.ts              # seedAuth() helper — injects JWT via /test/token + localStorage
  test_auth_flow.ts        # Flow A: auth gate → onboarding → workspace → StatusBar label
  test_pipeline.ts         # Flow B: type prompt → forge → stage cards → artifact renders
  test_github_flow.ts      # Flow E: connect GitHub → repo picker → linked badge
  playwright.config.ts     # webServer, baseURL, viewport, timeouts
```

---

## Backend Integration Fixture Architecture

```python
# backend/tests/integration/conftest.py

@pytest.fixture(scope="module")
async def engine(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest.fixture(scope="module")
async def client(engine):
    app.dependency_overrides[get_session] = session_override(engine)
    app.dependency_overrides[get_provider] = lambda: MockProvider()
    async with AsyncClient(transport=ASGITransport(app=asgi_app),
                           base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture
def auth_headers():
    token = sign_access_token({"sub": "test-user-id", "email": "test@example.com"})
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def other_auth_headers():
    token = sign_access_token({"sub": "other-user-id", "email": "other@example.com"})
    return {"Authorization": f"Bearer {token}"}
```

---

## Backend Test Coverage Map

### `test_history_api.py`
| Test | Method | Path | Asserts |
|---|---|---|---|
| happy path list | GET | `/api/history` | envelope shape, items belong to user |
| user isolation | GET | `/api/history` | other user's records not visible |
| filter min_score | GET | `/api/history?min_score=8` | only scores ≥ 8 returned |
| filter max_score | GET | `/api/history?max_score=5` | only scores ≤ 5 returned |
| filter has_repo | GET | `/api/history?has_repo=true` | only repo-linked records |
| filter task_type | GET | `/api/history?task_type=instruction` | filtered correctly |
| filter status | GET | `/api/history?status=completed` | filtered correctly |
| pagination | GET | `/api/history?limit=2&offset=0` | `has_more=true`, `next_offset=2` |
| soft delete | DELETE | `/api/history/{id}` | `deleted_at` set, not in list |
| delete wrong user | DELETE | `/api/history/{id}` | 404 |
| delete no auth | DELETE | `/api/history/{id}` | 401 |
| list trash | GET | `/api/history/trash` | only deleted-within-7-days items |
| trash empty after window | GET | `/api/history/trash` | item past 7 days not shown |
| restore happy path | POST | `/api/history/{id}/restore` | `deleted_at` cleared, back in list |
| restore wrong user | POST | `/api/history/{id}/restore` | 404 |
| restore not in trash | POST | `/api/history/{id}/restore` | 404 |
| stats scoped | GET | `/api/history/stats` | totals match only current user |

### `test_optimize_api.py`
| Test | Asserts |
|---|---|
| POST /api/optimize SSE stream | opens, emits `stage_start`/`stage_complete` events, final event has optimized prompt |
| SSE persists record | optimization retrievable via GET after stream closes |
| GET /api/optimize/{id} | full record shape, all fields present |
| GET unknown id | 404 |
| GET no auth | 401 |
| PATCH allowed fields | title/tags updated, reflected in GET |
| PATCH unknown field | 422 |

### `test_auth_api.py`
| Test | Asserts |
|---|---|
| GET /auth/me | returns display_name, email, github_login, avatar_url |
| GET /auth/me no auth | 401 |
| PATCH /auth/me display_name | updated, reflected in GET |
| PATCH /auth/me email | updated, reflected in GET |
| PATCH /auth/me over max length | 422 |
| POST /auth/refresh valid cookie | new access token returned |
| POST /auth/refresh no cookie | 401 |

### `test_github_api.py`
| Test | Asserts |
|---|---|
| GET /auth/github/status no session | `{connected: false}` |
| GET /auth/github/me no session | `{connected: false}` |
| GET /auth/github/me with token+user | `avatar_url` from User record (not token) |

### `test_providers_api.py`
| Test | Asserts |
|---|---|
| GET /api/providers/detect | returns `providers` map with `available` booleans |
| GET /api/providers/status | returns `{healthy: bool, message: str}` |
| GET /api/settings | returns settings object |
| PATCH /api/settings → GET | round-trip persists change |
| GET /api/health | `{status: "ok"}` |

### `test_mcp_api.py`
| Test | Asserts |
|---|---|
| POST /mcp initialize | returns `Mcp-Session-Id` header |
| tools/list | exactly 16 tools, each has all 4 annotation fields |
| list_optimizations | returns pagination envelope |
| delete_optimization + restore_optimization | round-trip: delete → appears in list_trash → restore → back in list |

---

## Playwright E2E Flow Designs

### Flow A — `test_auth_flow.ts`
1. Navigate to `/` → assert `AuthGate` visible, workbench not rendered
2. Call `seedAuth(page, { isNewUser: true })`
3. Reload → assert workbench layout visible (ActivityBar, Navigator, StatusBar)
4. Assert StatusBar user label is not "JWT" fallback
5. Assert `OnboardingModal` appears
6. Fill display name input → click Continue
7. Assert modal gone, StatusBar reflects new display name

### Flow B — `test_pipeline.ts`
1. `seedAuth(page)` (existing user, skip onboarding)
2. Assert `PromptEdit` textarea visible
3. Type sample prompt → click Forge button
4. Assert pipeline stage cards render in order: Explore → Analyze → Strategy → Optimize → Validate
5. Assert at least one stage reaches completed state (check for success indicator)
6. Assert `ForgeArtifact` panel shows optimized text
7. Assert entry appears in `NavigatorHistory` list

### Flow E — `test_github_flow.ts`
1. `seedAuth(page)`
2. Open Settings panel
3. Assert GitHub section shows "Not connected"
4. Intercept `/auth/github/login` redirect → seed connected state via `POST /test/github/seed`
5. Navigate to `/?auth_complete=1` with valid session cookie
6. Assert Settings GitHub section shows username + green connected dot
7. Assert repo picker appears in ContextBar
8. Click repo → assert linked badge renders in ContextBar

---

## Test-Only Backend Endpoint

Mounted only when `ENV=test`:

```python
# backend/app/routers/test_helpers.py (never imported in production)
@router.post("/test/token")
async def issue_test_token(body: TestTokenRequest, session=Depends(get_session)):
    """Issue a pre-signed JWT for E2E tests. Only available when ENV=test."""
    user = await upsert_test_user(session, body.email, body.is_new)
    token = sign_access_token({"sub": user.id, "email": user.email})
    return {"access_token": token}

@router.post("/test/github/seed")
async def seed_github_connection(body: GitHubSeedRequest, session=Depends(get_session)):
    """Seed a fake GitHub token for E2E auth flow testing."""
    ...
```

In `main.py`:
```python
if settings.env == "test":
    from app.routers.test_helpers import router as test_router
    app.include_router(test_router)
```

---

## CI Workflow (`.github/workflows/integration.yml`)

```yaml
name: Integration Tests

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  backend-integration:
    name: backend-integration
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.txt
      - run: pip install -r requirements.txt
      - run: pytest tests/integration/ -q --tb=short

  frontend-e2e:
    name: frontend-e2e
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
        working-directory: backend
      - uses: actions/setup-node@v4
        with: { node-version: "24", cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
        working-directory: frontend
      - run: npx playwright install --with-deps chromium
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
      - run: npx playwright test
        working-directory: frontend
        env:
          CI: true
          DATABASE_URL: sqlite+aiosqlite:///./e2e_test.db
          SECRET_KEY: e2e-test-secret-ci
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: frontend/playwright-report/
          retention-days: 7
```

---

## Out of Scope

- Visual regression testing (Percy, Chromatic)
- Load / performance testing
- Cross-browser testing (Chromium only in CI; run others locally)
- Mobile viewport testing
