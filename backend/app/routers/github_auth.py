"""GitHub OAuth flow — login, callback, me, logout."""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import GitHubToken, LinkedRepo
from app.services.github_client import GitHubClient
from app.services.github_service import GitHubService


def _safe_github_json(resp: httpx.Response, *, what: str) -> dict[str, Any]:
    """Defensively parse a GitHub OAuth JSON response.

    GitHub OAuth endpoints occasionally return non-JSON bodies during
    outages, rate-limiting, or device-code expiry — most commonly an
    empty body that raises ``JSONDecodeError`` on ``resp.json()``. The
    unhandled exception bubbles up to ASGI as a 500 with no CORS headers
    attached, which the browser then surfaces as a misleading
    ``"blocked by CORS policy"`` error.

    This helper unifies parsing across all four OAuth call sites
    (`request_device_code`, `poll_device_code`, `auth/login` callback,
    and `_refresh_access_token`). On parse failure or non-2xx + non-JSON,
    raises a clean ``HTTPException(502)`` so the response flows through
    CORS middleware normally and the client sees a proper error.
    """
    body = resp.text or ""
    try:
        parsed = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "GitHub %s returned non-JSON body (status=%s, len=%d): %r",
            what, resp.status_code, len(body), body[:200],
        )
        raise HTTPException(
            status_code=502,
            detail=f"GitHub returned an invalid response for {what}. Try again in a few seconds.",
        ) from exc
    if not isinstance(parsed, dict):
        logger.warning(
            "GitHub %s returned non-object JSON (status=%s, type=%s): %r",
            what, resp.status_code, type(parsed).__name__, str(parsed)[:200],
        )
        raise HTTPException(
            status_code=502,
            detail=f"GitHub returned an unexpected response shape for {what}.",
        )
    return parsed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["github"])


def _is_secure() -> bool:
    """Return True when FRONTEND_URL uses HTTPS (production)."""
    return bool(settings.FRONTEND_URL and settings.FRONTEND_URL.startswith("https://"))


class LoginUrlResponse(BaseModel):
    url: str = Field(description="GitHub OAuth authorization URL to redirect the user to.")


class GitHubUserResponse(BaseModel):
    login: str = Field(description="GitHub username.")
    avatar_url: str | None = Field(default=None, description="GitHub avatar image URL.")
    github_user_id: str | None = Field(default=None, description="GitHub numeric user ID as string.")


class OkResponse(BaseModel):
    ok: bool = Field(default=True, description="Operation success indicator.")


async def _refresh_token_if_expired(
    token_row: GitHubToken,
    db: AsyncSession,
) -> bool:
    """Refresh an expired GitHub access token using the stored refresh token.

    Returns True if refresh succeeded (token_row is updated in-place),
    False if refresh is not possible or failed.
    """
    # Check if token has expired
    if not token_row.expires_at:
        return False  # No expiry tracked — non-expiring token or legacy
    if token_row.expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        return False  # Not expired yet

    # Need refresh — check if we have a refresh token
    if not token_row.refresh_token_encrypted:
        logger.warning("GitHub access token expired but no refresh token stored (session=%s)", token_row.session_id[:8])
        return False

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    refresh_token = github_svc.decrypt_token(token_row.refresh_token_encrypted)

    # Exchange refresh token for new access token
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
            )
            data = _safe_github_json(resp, what="token refresh")
    except HTTPException:
        return False
    except Exception as exc:
        logger.warning("GitHub token refresh request failed: %s", exc)
        return False

    new_access_token = data.get("access_token")
    if not new_access_token:
        logger.warning("GitHub token refresh returned no access_token: %s", data.get("error", "unknown"))
        return False

    # v0.4.14 cycle 3e.1: route the token-row update through the WriteQueue.
    # The caller's ``db`` is bound to the read engine and cannot be shared
    # with the writer session, so we re-fetch the row inside the queue
    # callback and apply the same field updates there. After the queued
    # work commits, we ``db.refresh()`` the caller-side row so any
    # downstream reads see the new state.
    new_refresh_token = data.get("refresh_token")
    new_expires_in = data.get("expires_in")
    new_refresh_expires_in = data.get("refresh_token_expires_in")
    captured_session_id = token_row.session_id

    async def _do_refresh(write_db: AsyncSession) -> bool:
        result = await write_db.execute(
            select(GitHubToken).where(GitHubToken.session_id == captured_session_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.token_encrypted = github_svc.encrypt_token(new_access_token)
        if new_refresh_token:
            row.refresh_token_encrypted = github_svc.encrypt_token(new_refresh_token)
        if new_expires_in:
            row.expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=int(new_expires_in)
            )
        if new_refresh_expires_in:
            row.refresh_token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=int(new_refresh_expires_in)
            )
        await write_db.commit()
        return True

    from app.tools._shared import get_write_queue
    refreshed = await get_write_queue().submit(
        _do_refresh, operation_label="github_token_refresh",
    )
    if refreshed:
        await db.refresh(token_row)
        logger.info("GitHub token refreshed (session=%s)", token_row.session_id[:8])
    return refreshed


async def _get_session_token(request: Request, db: AsyncSession) -> tuple[str, str]:
    """Get session_id and decrypted token from request cookie.

    Automatically refreshes the access token via the stored refresh token
    if the access token has expired.  This handles GitHub Apps with
    "Expire user authorization tokens" enabled (8-hour default expiry).
    """
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(
        select(GitHubToken).where(GitHubToken.session_id == session_id)
    )
    token_row = result.scalar_one_or_none()
    if not token_row:
        raise HTTPException(401, "Not authenticated")

    # Auto-refresh if access token has expired
    if token_row.expires_at and token_row.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        refreshed = await _refresh_token_if_expired(token_row, db)
        if not refreshed:
            raise HTTPException(401, "GitHub token expired and refresh failed. Please reconnect.")

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    return session_id, github_svc.decrypt_token(token_row.token_encrypted)


@router.get("/auth/login")
async def github_login(response: Response) -> LoginUrlResponse:
    """Generate OAuth URL, set state cookie, return URL."""
    state = secrets.token_urlsafe(32)
    response.set_cookie(
        "github_oauth_state", state, httponly=True, max_age=600,
        samesite="lax", secure=_is_secure(),
    )
    github_svc = GitHubService(
        secret_key=settings.resolve_secret_key(),
        client_id=settings.GITHUB_OAUTH_CLIENT_ID,
    )
    url = github_svc.build_oauth_url(state=state)
    return LoginUrlResponse(url=url)


@router.get("/auth/callback")
async def github_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Exchange code for token, encrypt, store in DB."""
    import hmac

    cookie_state = request.cookies.get("github_oauth_state")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(
            400,
            "Invalid OAuth state. The login link may have expired. Please try logging in again.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        data = _safe_github_json(resp, what="OAuth token exchange")

    access_token = data.get("access_token")
    if not access_token:
        error_desc = data.get("error_description", "unknown error")
        logger.warning("GitHub OAuth token exchange failed: %s", error_desc)
        raise HTTPException(
            status_code=400,
            detail="Authentication failed. Please try again.",
        )

    github_client = GitHubClient()
    user = await github_client.get_user(access_token)

    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    encrypted = github_svc.encrypt_token(access_token)

    # Token expiry + refresh token (for GitHub Apps with expiring tokens)
    now = datetime.now(timezone.utc)
    expires_in = int(data["expires_in"]) if data.get("expires_in") else None
    expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    refresh_encrypted = (
        github_svc.encrypt_token(data["refresh_token"]) if data.get("refresh_token") else None
    )
    refresh_exp = int(data["refresh_token_expires_in"]) if data.get("refresh_token_expires_in") else None
    refresh_expires_at = now + timedelta(seconds=refresh_exp) if refresh_exp else None

    session_id = request.cookies.get("session_id") or secrets.token_urlsafe(32)

    # v0.4.14 cycle 3e.2: persist the token row and audit log atomically
    # through submit_batch. Both writes share one writer session and one
    # transaction. We snapshot every closure dependency BEFORE declaring
    # the work_fns so they reference only captured_* values — never the
    # outer ``request``/``db``/``user`` (would cross sessions).
    captured_session_id = session_id
    captured_user_login = user.get("login")
    captured_actor_ip = request.client.host if request.client else None
    captured_encrypted = encrypted
    captured_expires_at = expires_at
    captured_refresh_encrypted = refresh_encrypted
    captured_refresh_expires_at = refresh_expires_at
    captured_avatar_url = user.get("avatar_url")
    captured_user_id = str(user.get("id", ""))

    async def _persist_token(write_db: AsyncSession) -> None:
        existing_q = await write_db.execute(
            select(GitHubToken).where(GitHubToken.session_id == captured_session_id)
        )
        existing = existing_q.scalar_one_or_none()
        if existing:
            existing.token_encrypted = captured_encrypted
            existing.github_login = captured_user_login
            existing.github_user_id = captured_user_id
            existing.avatar_url = captured_avatar_url
            existing.expires_at = captured_expires_at
            existing.refresh_token_encrypted = captured_refresh_encrypted
            existing.refresh_token_expires_at = captured_refresh_expires_at
        else:
            row = GitHubToken(
                session_id=captured_session_id,
                token_encrypted=captured_encrypted,
                github_login=captured_user_login,
                github_user_id=captured_user_id,
                avatar_url=captured_avatar_url,
                expires_at=captured_expires_at,
                refresh_token_encrypted=captured_refresh_encrypted,
                refresh_token_expires_at=captured_refresh_expires_at,
            )
            write_db.add(row)
        # NO commit — submit_batch owns the transaction.

    # NOTE: log_event canNOT be called inside submit_batch (it commits
    # internally on both queue + legacy paths, which would either trigger
    # SubmitBatchCommitError or WriteQueueReentrancyError). The audit row
    # is inserted directly via the AuditLog model.
    async def _record_audit(write_db: AsyncSession) -> None:
        from app.models import AuditLog
        entry = AuditLog(
            action="github_login",
            actor_ip=captured_actor_ip,
            actor_session=None,
            detail={"github_login": captured_user_login},
            outcome="success",
        )
        write_db.add(entry)
        # NO commit — submit_batch owns the transaction.

    from app.tools._shared import get_write_queue
    await get_write_queue().submit_batch(
        [_persist_token, _record_audit],
        operation_label="github_oauth_callback",
    )

    logger.info("GitHub OAuth callback completed: user=%s", captured_user_login)

    # Redirect to frontend app after successful OAuth
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    redirect = RedirectResponse(
        url=f"{frontend_url}/app?github_auth=success",
        status_code=302,
    )
    redirect.set_cookie(
        "session_id", session_id, httponly=True, max_age=86400 * 14,
        samesite="lax", secure=_is_secure(), path="/api",
    )
    return redirect


@router.get("/auth/me")
async def github_me(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> GitHubUserResponse:
    """Return current user info, validating the token is still live with GitHub.

    If the stored token has been revoked or expired, clean it up and return 401
    so the frontend shows the reconnect prompt instead of stale cached data.
    """
    session_id, token = await _get_session_token(request, db)
    # Validate token is still accepted by GitHub
    client = GitHubClient()
    try:
        user = await client.get_user(token)
    except Exception:
        # Token revoked/expired — clean up all stale data for this session:
        # token, linked repo, and session cookie.  This prevents orphan linked
        # repos that reference a dead token, which is the root cause of the
        # confusing "connected in Info tab but 401 on Files" state.
        logger.warning(
            "GitHub token validation failed for session %s — cleaning up",
            session_id[:8],
        )
        # v0.4.14 cycle 3e.3: route the cleanup deletes through the
        # WriteQueue. We snapshot session_id before declaring the
        # closure so the work_fn references no outer state, then
        # re-fetch + delete both rows inside the writer session.
        captured_session_id = session_id

        async def _do_cleanup(write_db: AsyncSession) -> None:
            res = await write_db.execute(
                select(GitHubToken).where(
                    GitHubToken.session_id == captured_session_id
                )
            )
            row = res.scalar_one_or_none()
            if row:
                await write_db.delete(row)
            res = await write_db.execute(
                select(LinkedRepo).where(
                    LinkedRepo.session_id == captured_session_id
                )
            )
            linked = res.scalar_one_or_none()
            if linked:
                await write_db.delete(linked)
            await write_db.commit()

        from app.tools._shared import get_write_queue
        await get_write_queue().submit(
            _do_cleanup,
            operation_label="github_auth_me_revoke_cleanup",
        )
        response.delete_cookie("session_id", path="/api")
        raise HTTPException(401, "GitHub token expired or revoked. Please reconnect.")
    # Update cached user info if it changed.
    # v0.4.14 cycle 3e.4: route the GitHubToken field updates through the
    # WriteQueue. We snapshot session_id + the new login/avatar/user_id
    # before declaring the closure so the work_fn touches no outer state.
    captured_session_id = session_id
    captured_login = user.get("login")
    captured_avatar = user.get("avatar_url")
    captured_user_id = str(user.get("id", ""))

    async def _do_user_update(write_db: AsyncSession) -> None:
        res = await write_db.execute(
            select(GitHubToken).where(
                GitHubToken.session_id == captured_session_id
            )
        )
        row = res.scalar_one_or_none()
        if row:
            # Preserve existing values when source is None/empty.
            row.github_login = captured_login or row.github_login
            row.avatar_url = captured_avatar or row.avatar_url
            row.github_user_id = captured_user_id or row.github_user_id
            await write_db.commit()

    from app.tools._shared import get_write_queue
    await get_write_queue().submit(
        _do_user_update,
        operation_label="github_auth_me_user_info_update",
    )
    return GitHubUserResponse(
        login=user.get("login", ""),
        avatar_url=user.get("avatar_url"),
        github_user_id=str(user.get("id", "")),
    )


@router.post("/auth/logout")
async def github_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> OkResponse:
    """Delete token from DB and clear session cookie."""
    session_id = request.cookies.get("session_id")
    if session_id:
        # v0.4.14 cycle 3e.5: route the GitHubToken delete through the
        # WriteQueue. We snapshot session_id before declaring the
        # closure so the work_fn references no outer state, then
        # re-fetch + delete inside the writer session.
        captured_session_id = session_id

        async def _do_logout(write_db: AsyncSession) -> None:
            res = await write_db.execute(
                select(GitHubToken).where(
                    GitHubToken.session_id == captured_session_id
                )
            )
            row = res.scalar_one_or_none()
            if row:
                await write_db.delete(row)
                await write_db.commit()

        from app.tools._shared import get_write_queue
        await get_write_queue().submit(
            _do_logout,
            operation_label="github_logout_token_delete",
        )
    response.delete_cookie("session_id", path="/api")

    # Audit log
    try:
        from app.services.audit_logger import log_event

        await log_event(
            db=db,
            action="github_logout",
            actor_ip=request.client.host if request.client else None,
            outcome="success",
        )
    except Exception:
        logger.debug("Audit log write failed", exc_info=True)

    return OkResponse()


# ---------------------------------------------------------------------------
# Device Flow — zero-config OAuth (no client secret, no callback URL)
# ---------------------------------------------------------------------------


class DevicePollRequest(BaseModel):
    device_code: str = Field(description="Device code from the /auth/device request.")


class DevicePollResponse(BaseModel):
    status: str = Field(description="authorization_pending | slow_down | expired_token | success")
    user: GitHubUserResponse | None = None


@router.post("/auth/device")
async def request_device_code():
    """Start GitHub Device Flow. Returns user_code for the user to enter at github.com/login/device."""
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        raise HTTPException(
            500,
            "GITHUB_OAUTH_CLIENT_ID not configured. Set it in .env to enable GitHub integration.",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            data={
                "client_id": client_id,
                "scope": "repo read:user",
            },
            headers={"Accept": "application/json"},
        )
        data = _safe_github_json(resp, what="device code request")

    if "user_code" not in data:
        error_desc = data.get("error_description", "Failed to start device flow")
        logger.warning("GitHub device flow request failed: %s", error_desc)
        raise HTTPException(400, error_desc)

    logger.info("GitHub device flow started: user_code=%s", data.get("user_code"))
    return data  # {device_code, user_code, verification_uri, expires_in, interval}


@router.post("/auth/device/poll")
async def poll_device_code(
    body: DevicePollRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> DevicePollResponse:
    """Poll GitHub for device authorization status. Call repeatedly at the specified interval."""
    client_id = settings.GITHUB_OAUTH_CLIENT_ID
    if not client_id:
        raise HTTPException(500, "GITHUB_OAUTH_CLIENT_ID not configured.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "device_code": body.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = _safe_github_json(resp, what="device code poll")

    # Still waiting or error
    if "error" in data:
        return DevicePollResponse(status=data["error"])

    # Success — got access token
    access_token = data.get("access_token")
    if not access_token:
        return DevicePollResponse(status="error")

    # Fetch user info
    github_client = GitHubClient()
    user = await github_client.get_user(access_token)

    # Encrypt and store token + refresh token (for expiring GitHub App tokens)
    github_svc = GitHubService(secret_key=settings.resolve_secret_key())
    encrypted = github_svc.encrypt_token(access_token)

    # Compute expiry timestamps from GitHub's response
    now = datetime.now(timezone.utc)
    expires_in = int(data["expires_in"]) if data.get("expires_in") else None
    expires_at = now + timedelta(seconds=expires_in) if expires_in else None
    refresh_encrypted = (
        github_svc.encrypt_token(data["refresh_token"]) if data.get("refresh_token") else None
    )
    refresh_exp = int(data["refresh_token_expires_in"]) if data.get("refresh_token_expires_in") else None
    refresh_expires_at = now + timedelta(seconds=refresh_exp) if refresh_exp else None

    if data.get("refresh_token"):
        logger.info(
            "GitHub token has refresh support (expires_in=%s, refresh_expires_in=%s)",
            data.get("expires_in"), data.get("refresh_token_expires_in"),
        )

    session_id = request.cookies.get("session_id") or secrets.token_urlsafe(32)

    # v0.4.14 cycle 3e.6: persist the device-flow token row and audit log
    # atomically through submit_batch. Both writes share one writer
    # session and one transaction. We snapshot every closure dependency
    # BEFORE declaring the work_fns so they reference only captured_*
    # values — never the outer ``request``/``db``/``user``.
    captured_session_id = session_id
    captured_user_login = user.get("login")
    captured_actor_ip = request.client.host if request.client else None
    captured_encrypted = encrypted
    captured_expires_at = expires_at
    captured_refresh_encrypted = refresh_encrypted
    captured_refresh_expires_at = refresh_expires_at
    captured_avatar_url = user.get("avatar_url")
    captured_user_id = str(user.get("id", ""))

    async def _persist_token(write_db: AsyncSession) -> None:
        existing_q = await write_db.execute(
            select(GitHubToken).where(
                GitHubToken.session_id == captured_session_id
            )
        )
        existing = existing_q.scalar_one_or_none()
        if existing:
            existing.token_encrypted = captured_encrypted
            existing.github_login = captured_user_login
            existing.github_user_id = captured_user_id
            existing.avatar_url = captured_avatar_url
            existing.expires_at = captured_expires_at
            existing.refresh_token_encrypted = captured_refresh_encrypted
            existing.refresh_token_expires_at = captured_refresh_expires_at
        else:
            row = GitHubToken(
                session_id=captured_session_id,
                token_encrypted=captured_encrypted,
                github_login=captured_user_login,
                github_user_id=captured_user_id,
                avatar_url=captured_avatar_url,
                expires_at=captured_expires_at,
                refresh_token_encrypted=captured_refresh_encrypted,
                refresh_token_expires_at=captured_refresh_expires_at,
            )
            write_db.add(row)
        # NO commit — submit_batch owns the transaction.

    # NOTE: log_event canNOT be called inside submit_batch (it commits
    # internally on both queue + legacy paths). The audit row is
    # inserted directly via the AuditLog model.
    async def _record_audit(write_db: AsyncSession) -> None:
        from app.models import AuditLog
        entry = AuditLog(
            action="github_login",
            actor_ip=captured_actor_ip,
            actor_session=None,
            detail={"github_login": captured_user_login, "flow": "device"},
            outcome="success",
        )
        write_db.add(entry)
        # NO commit — submit_batch owns the transaction.

    # Plan-prescribed label per cycle 3e.6 (`github_token_revoke`); kept
    # as the spec-required telemetry tag even though semantically this
    # site is a device-flow login (token upsert), not a revoke. See
    # report for plan-vs-source label discrepancy callout.
    from app.tools._shared import get_write_queue
    await get_write_queue().submit_batch(
        [_persist_token, _record_audit],
        operation_label="github_token_revoke",
    )

    response.set_cookie(
        "session_id", session_id, httponly=True, max_age=86400 * 14,
        samesite="lax", secure=_is_secure(), path="/api",
    )
    logger.info("GitHub device flow completed: user=%s", captured_user_login)

    return DevicePollResponse(
        status="success",
        user=GitHubUserResponse(
            login=user.get("login") or "",
            avatar_url=user.get("avatar_url"),
        ),
    )
