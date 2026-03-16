# Auth Security Hardening — TDD Design Document

**Date:** 2026-03-09
**Scope:** Authentication, session management, onboarding — 11 TDD cycles
**Approach:** TDD micro-iterations (RED → GREEN → REFACTOR per gap)
**Baseline:** OWASP ASVS Level 2

---

## Problem Statement

A security-focused audit of the authentication system identified 8 pre-specified gaps
plus 4 additional findings. This document captures the confirmed gap analysis, the
approved TDD cycle design, and the implementation order.

---

## Phase 1: Gap Audit

### Pre-specified Gaps

| # | Gap | Severity | Confirmed | Location |
|---|-----|----------|-----------|----------|
| 1 | No structured onboarding flow | Medium | ✅ | `github_auth.py:28-46, 244` |
| 2 | Anemic User model | Medium | ✅ | `models/auth.py:23-38` |
| 3 | Incomplete multi-device logout | High | ✅ | `github_auth.py:299-307` |
| 4 | Session fixation vulnerability | High | ✅ | `github_auth.py:75-80` |
| 5 | No rate limiting on auth endpoints | High | ✅ | All auth routers |
| 6 | No fallback auth / email verification | Low | ✅ | Whole auth system — deferred (architectural) |
| 7 | Access token revocation gap | High | ✅ | `dependencies/auth.py:68-75` |
| 8 | No manual GitHub token refresh endpoint | Low | ✅ | `github_service.py:147-180` |

### Additional Findings

| # | Gap | Severity | Location |
|---|-----|----------|----------|
| A | Access token in URL query param (ASVS §3.5.2) | High | `github_auth.py:244` |
| B | `JWT_COOKIE_SECURE` defaults to `False`, no startup enforcement | Medium | `config.py:57` |
| C | Error information leakage — `"User not found"` is an oracle | Low | `routers/auth.py:112` |
| D | `SameSite=Lax` on refresh cookie — `Strict` is appropriate | Low | `github_auth.py:55`, `auth.py:124` |

Gap 6 (fallback auth) is deferred — it requires architectural changes outside the
authentication/session scope and is not required for ASVS Level 2.

---

## Phase 2: TDD Cycle Design

### Execution Order

Ordered by severity + dependency (schema changes that unlock subsequent gaps first):

| Order | Gap | Why here |
|-------|-----|----------|
| 1 | Gap A — token in URL | Standalone High; no schema deps |
| 2 | Gap 7 — revocation gap | Adds `device_id` to `RefreshToken`; unblocks Gap 3 |
| 3 | Gap 3 — multi-device logout | Depends on Gap 7 `device_id` schema |
| 4 | Gap 4 — session fixation | Standalone; no schema change |
| 5 | Gap 5 — rate limiting | Standalone; new dependency (`slowapi`) |
| 6 | Gap B — cookie Secure enforcement | Config + startup check |
| 7 | Gap 2 — User model enrichment | DB migration; unblocks Gap 1 |
| 8 | Gap 1 — onboarding flow | Depends on Gap 2 columns |
| 9 | Gap 8 — manual GH token refresh | New endpoint; Low priority |
| 10 | Gap C — error leakage | Low; message unification |
| 11 | Gap D — SameSite Strict | Low; evaluate regression risk |

---

## Phase 3: TDD Cycle Specs

### Cycle 1 — Gap A: Token in URL

**RED**
- `test_callback_redirect_has_no_access_token_in_url` — assert redirect URL contains no `access_token` param
- `test_auth_token_endpoint_returns_access_token` — `GET /auth/token` with valid one-time cookie returns `{access_token}`
- `test_auth_token_endpoint_clears_cookie_after_read` — second call to `GET /auth/token` returns 401

**GREEN**
- Change callback redirect to `FRONTEND_URL/auth/callback` (no token in URL)
- Set access token in a short-lived `__Host-at` httponly cookie (`max_age=30`, `SameSite=Strict`)
- Add `GET /auth/token` endpoint: reads cookie, returns JSON, clears cookie

**REFACTOR**
- Enforce `__Host-` prefix, `max_age=30`, `SameSite=Strict` on one-time cookie
- Update `AuthGate.svelte` to call `GET /auth/token` on `/auth/callback` route, store via `auth.setToken()`
- Delete one-time cookie on read (set `max_age=0`)

**Files:** `routers/github_auth.py`, `routers/auth.py`, `frontend/src/lib/components/layout/AuthGate.svelte`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 2 — Gap 7: Access Token Revocation Gap

**RED**
- `test_device_a_logout_does_not_unrevoke_via_device_b_refresh` — after A logs out, B refreshes (new RT), A's access token is still rejected
- `test_get_current_user_uses_device_id_not_most_recent_rt` — revocation checks the RT matching the token's `device_id` claim

**GREEN**
- Add `device_id: Text` nullable column to `RefreshToken`
- Embed `device_id` in access token JWT claims
- `get_current_user` queries RT by `device_id` from JWT, falls back to most-recent for tokens without `device_id`
- `issue_jwt_pair` accepts/generates `device_id`

**REFACTOR**
- Add `idx_refresh_tokens_device_id` index
- Update `sign_access_token` signature to include `device_id`
- Add `device_id` to `_migrate_add_missing_columns` migration
- Backward compat: tokens without `device_id` claim use legacy most-recent-RT check

**Files:** `models/auth.py`, `services/auth_service.py`, `utils/jwt.py`, `dependencies/auth.py`, `database.py`
**Migration:** `_migrate_add_missing_columns` — add `device_id TEXT` nullable to `refresh_tokens`
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 3 — Gap 3: Multi-Device Logout

**RED**
- `test_logout_revokes_all_refresh_tokens_for_user` — after logout, all RTs for user are `revoked=True`
- `test_logout_all_devices_endpoint_revokes_all` — `DELETE /auth/sessions` revokes all RTs across devices

**GREEN**
- Remove `.limit(1)` from logout RT query; bulk-update all non-revoked RTs for `user_id`
- Add `DELETE /auth/sessions` endpoint (logout all devices)

**REFACTOR**
- `DELETE /auth/github/logout` = current-device logout (uses `device_id` from JWT to revoke only that RT)
- `DELETE /auth/sessions` = nuclear option — revokes all RTs for user
- Wire "logout everywhere" into frontend session management UI

**Files:** `routers/github_auth.py`, `routers/auth.py`
**Migration:** None (uses Gap 7 schema)
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 4 — Gap 4: Session Fixation

**RED**
- `test_session_id_rotates_after_oauth_callback` — session ID in cookie differs between pre-auth and post-auth
- `test_github_token_linked_to_new_session_id` — `GitHubToken.session_id` uses the rotated ID

**GREEN**
- In callback, after successful auth: `request.session.clear()`, regenerate `session_id = str(uuid4())`, repopulate session data
- Update `GitHubToken.session_id` to the new session ID before clearing the old one

**REFACTOR**
- Extract `_rotate_session(request, **data) -> str` helper
- Ensure no dangling GitHubToken references to old session ID

**Files:** `routers/github_auth.py`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 5 — Gap 5: Rate Limiting

**RED**
- `test_login_endpoint_rate_limited_at_20_per_minute`
- `test_callback_endpoint_rate_limited_at_10_per_minute`
- `test_refresh_endpoint_rate_limited_at_60_per_minute`
- `test_rate_limit_response_has_structured_error_code`

**GREEN**
- Add `slowapi` to dependencies
- Apply `@limiter.limit("20/minute")` to `/auth/github/login`
- Apply `@limiter.limit("10/minute")` to `/auth/github/callback`
- Apply `@limiter.limit("60/minute")` to `/auth/jwt/refresh`
- Add `RateLimitExceeded` handler returning `{"code": "RATE_LIMIT_EXCEEDED", "message": "..."}`

**REFACTOR**
- Move limits to `config.py`: `RATE_LIMIT_AUTH_LOGIN`, `RATE_LIMIT_AUTH_CALLBACK`, `RATE_LIMIT_JWT_REFRESH`
- Register handler in `main.py` lifespan

**Files:** `routers/github_auth.py`, `routers/auth.py`, `main.py`, `config.py`
**Migration:** None
**New dependency:** `slowapi`
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 6 — Gap B: Cookie Secure Enforcement

**RED**
- `test_startup_warns_when_jwt_cookie_secure_false_in_production` — `FRONTEND_URL=https://...` + `JWT_COOKIE_SECURE=False` raises `ValueError` or logs `CRITICAL`
- `test_startup_allows_jwt_cookie_secure_false_on_localhost` — localhost FRONTEND_URL is exempt

**GREEN**
- Add `_check_production_security()` in `config.py` `model_post_init`
- If `FRONTEND_URL` does not start with `http://localhost` and `JWT_COOKIE_SECURE=False`, log `CRITICAL` warning

**REFACTOR**
- Optionally raise `ValueError` to hard-block startup (make behavior configurable via `STRICT_SECURITY_CHECK: bool = False`)

**Files:** `config.py`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 7 — Gap 2: User Model Enrichment

**RED**
- `test_user_model_has_email_field`
- `test_user_model_has_avatar_url_field`
- `test_user_model_has_display_name_field`
- `test_user_model_has_onboarding_completed_at_field`
- `test_user_model_has_last_login_at_field`
- `test_upsert_user_sets_last_login_at_on_login`
- `test_upsert_user_caches_avatar_url_from_github`

**GREEN**
- Add 5 nullable columns to `User`: `email`, `avatar_url`, `display_name`, `onboarding_completed_at`, `last_login_at`
- Add to `_migrate_add_missing_columns`
- Update `_upsert_user` to accept `avatar_url` from GitHub user data, set `last_login_at = now()`

**REFACTOR**
- Add `GET /auth/me` endpoint returning full profile
- Add `PATCH /auth/me` for `display_name` updates
- `github_me` reads `avatar_url` from `User` (not `GitHubToken`) — single source of truth

**Files:** `models/auth.py`, `database.py`, `routers/github_auth.py`, `routers/auth.py`
**Migration:** `_migrate_add_missing_columns` — 5 new nullable columns on `users`
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 8 — Gap 1: Onboarding Flow

**RED**
- `test_upsert_user_returns_is_new_flag`
- `test_callback_redirect_includes_new_param_for_new_users`
- `test_callback_redirect_excludes_new_param_for_existing_users`
- Frontend: `test_auth_gate_shows_onboarding_modal_on_new_param`

**GREEN**
- Change `_upsert_user` to return `tuple[User, bool]` (`is_new`)
- Set `?new=1` in callback redirect when `is_new=True`
- Add `OnboardingModal.svelte` — welcome screen + display name form
- `AuthGate.svelte` renders modal when URL has `?new=1`

**REFACTOR**
- Onboarding completion calls `PATCH /auth/me` setting `display_name` + `onboarding_completed_at`
- On every login: if `onboarding_completed_at IS NULL`, add `?new=1` to redirect (re-triggers modal)

**Files:** `routers/github_auth.py`, `frontend/src/lib/components/layout/AuthGate.svelte`, `frontend/src/lib/components/layout/OnboardingModal.svelte` (new)
**Migration:** None (uses Gap 2 columns)
**Test file:** `backend/tests/test_auth_security.py`, frontend Vitest

---

### Cycle 9 — Gap 8: Manual GitHub Token Refresh

**RED**
- `test_github_token_refresh_endpoint_calls_refresh_user_token`
- `test_github_token_refresh_returns_expires_at`
- `test_github_token_refresh_skips_if_not_expiring_soon`

**GREEN**
- Add `POST /auth/github/token/refresh` endpoint
- Calls `refresh_user_token` if token expires within 30 min; updates DB record

**REFACTOR**
- Return `{refreshed: bool, expires_at: str}` — if token not near expiry, return `{refreshed: false, expires_at}`
- Add to `AuthGate.svelte` action menu (optional manual trigger)

**Files:** `routers/github_auth.py`, `services/github_service.py`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 10 — Gap C: Error Leakage

**RED**
- `test_refresh_user_not_found_and_token_not_found_return_same_message` — both cases return identical error body, preventing oracle attacks

**GREEN**
- Unify `"User not found"` and `"Refresh token not found"` → `"Authentication failed"` with code `ERR_TOKEN_INVALID`

**REFACTOR**
- Audit all auth 401 paths for distinguishable user-existence signals; standardize messages

**Files:** `routers/auth.py`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

### Cycle 11 — Gap D: SameSite Strict

**RED**
- `test_refresh_cookie_uses_samesite_strict`
- `test_one_time_token_cookie_uses_samesite_strict` (from Cycle 1)

**GREEN**
- Change `samesite="lax"` → `samesite="strict"` in `github_auth.py:55` and `auth.py:124`

**REFACTOR**
- Confirm `auth.svelte.ts` silent refresh uses `credentials: 'include'` — Strict does not break same-origin fetch
- Document why Strict is safe here (the refresh endpoint is never called via cross-site navigation)

**Files:** `routers/github_auth.py`, `routers/auth.py`
**Migration:** None
**Test file:** `backend/tests/test_auth_security.py`

---

## Constraints

1. **Preserve JWT format** — HS256, existing claim schema unchanged except adding `device_id`; backward compat for tokens without it
2. **Refresh token backward compatibility** — active tokens (up to 7-day lifetime) remain valid; `device_id` column is nullable
3. **Preserve GitHub OAuth App flow** — callback URL contract unchanged
4. **No breaking cookie changes** — `jwt_refresh_token` cookie name/path/domain unchanged
5. **SQLite compatibility** — all migrations use the existing `_migrate_add_missing_columns` pattern
6. **Scope boundary** — authentication, authorization, session management, onboarding only

---

## New Files

| File | Purpose |
|------|---------|
| `backend/tests/test_auth_security.py` | All 35+ RED tests, one file per TDD cycle section |
| `frontend/src/lib/components/layout/OnboardingModal.svelte` | Onboarding welcome modal (Cycle 8) |

## Modified Files

| File | Changes |
|------|---------|
| `backend/app/routers/github_auth.py` | Cycles 1, 3, 4, 7, 8, 9 |
| `backend/app/routers/auth.py` | Cycles 1, 3, 5, 10, 11 |
| `backend/app/dependencies/auth.py` | Cycle 2 |
| `backend/app/models/auth.py` | Cycles 2, 7 |
| `backend/app/services/auth_service.py` | Cycle 2 |
| `backend/app/utils/jwt.py` | Cycle 2 |
| `backend/app/database.py` | Cycles 2, 7 (migration) |
| `backend/app/config.py` | Cycles 5, 6 |
| `backend/app/main.py` | Cycle 5 (rate limit handler) |
| `frontend/src/lib/components/layout/AuthGate.svelte` | Cycles 1, 8 |
