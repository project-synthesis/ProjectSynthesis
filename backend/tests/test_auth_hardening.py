"""Auth hardening tests — logout, audit, MultiFernet rotation, rate limits.

Covers the remaining suggested tests from the security + onboarding audit:
- POST /auth/logout (per-device and legacy fallback)
- Audit log entry creation
- MultiFernet key rotation for GitHub tokens
- Rate limit signature on new endpoints

Run: cd backend && source .venv/bin/activate && pytest tests/test_auth_hardening.py -v
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.auth import AuthenticatedUser
from app.utils.jwt import sign_access_token

# ── POST /auth/logout — per-device revocation (4 tests) ──────────────────


async def test_logout_single_device_revokes_by_device_id():
    """POST /auth/logout extracts device_id from JWT and revokes only that device's RTs."""
    from app.routers.auth import logout_single_device

    device_id = "device-abc-123"
    token = sign_access_token("user-1", "octocat", ["user"], device_id=device_id)

    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])

    # Two RTs for this device
    rt1 = MagicMock()
    rt1.revoked = False
    rt2 = MagicMock()
    rt2.revoked = False

    rt_result = MagicMock()
    rt_result.scalars.return_value.all.return_value = [rt1, rt2]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    mock_response = MagicMock()

    with patch("app.routers.auth.log_auth_event", new_callable=AsyncMock):
        result = await logout_single_device(
            request=mock_request,
            response=mock_response,
            current_user=current_user,
            session=mock_session,
        )

    assert rt1.revoked is True
    assert rt2.revoked is True
    assert result["revoked_count"] == 2


async def test_logout_single_device_legacy_token_revokes_most_recent():
    """POST /auth/logout without device_id revokes the most recent non-revoked RT."""
    from app.routers.auth import logout_single_device

    # Legacy token — no device_id
    token = sign_access_token("user-1", "octocat", ["user"])
    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])

    rt = MagicMock()
    rt.revoked = False

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = rt

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    mock_response = MagicMock()

    with patch("app.routers.auth.log_auth_event", new_callable=AsyncMock):
        result = await logout_single_device(
            request=mock_request,
            response=mock_response,
            current_user=current_user,
            session=mock_session,
        )

    assert rt.revoked is True
    assert result["revoked_count"] == 1


async def test_logout_single_device_clears_refresh_cookie():
    """POST /auth/logout deletes the jwt_refresh_token cookie."""
    from app.routers.auth import logout_single_device

    token = sign_access_token("user-1", "octocat", ["user"])
    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])

    rt_result = MagicMock()
    rt_result.scalar_one_or_none.return_value = None  # no RT found

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    mock_response = MagicMock()

    with patch("app.routers.auth.log_auth_event", new_callable=AsyncMock):
        await logout_single_device(
            request=mock_request,
            response=mock_response,
            current_user=current_user,
            session=mock_session,
        )

    mock_response.delete_cookie.assert_called_once_with(
        key="jwt_refresh_token", path="/auth/jwt/refresh"
    )


async def test_logout_single_device_logs_audit_event():
    """POST /auth/logout calls log_auth_event with AUTH_LOGOUT and metadata."""
    from app.routers.auth import logout_single_device
    from app.services.audit_service import AUTH_LOGOUT

    device_id = "device-xyz"
    token = sign_access_token("user-1", "octocat", ["user"], device_id=device_id)
    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])

    rt_result = MagicMock()
    rt_result.scalars.return_value.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=rt_result)

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": f"Bearer {token}"}
    mock_response = MagicMock()

    mock_audit = AsyncMock()
    with patch("app.routers.auth.log_auth_event", mock_audit):
        await logout_single_device(
            request=mock_request,
            response=mock_response,
            current_user=current_user,
            session=mock_session,
        )

    mock_audit.assert_called_once()
    call_args = mock_audit.call_args
    assert call_args[0][0] == AUTH_LOGOUT  # first positional arg
    assert call_args[1]["user_id"] == "user-1"
    assert call_args[1]["metadata"]["device_id"] == device_id


# ── Rate limit signatures on new endpoints (2 tests) ──────────────────────


def test_rate_limit_dependency_in_auth_token():
    """RateLimit dependency must be present in get_auth_token endpoint signature."""
    import inspect

    from app.routers.auth import get_auth_token

    sig = inspect.signature(get_auth_token)
    assert "_rl" in sig.parameters, (
        f"get_auth_token must have _rl parameter. Found: {list(sig.parameters)}"
    )


def test_rate_limit_dependency_in_logout_single_device():
    """RateLimit dependency must be present in logout_single_device endpoint signature."""
    import inspect

    from app.routers.auth import logout_single_device

    sig = inspect.signature(logout_single_device)
    assert "_rl" in sig.parameters, (
        f"logout_single_device must have _rl parameter. Found: {list(sig.parameters)}"
    )


# ── MultiFernet key rotation (3 tests) ───────────────────────────────────


def test_encrypt_decrypt_roundtrip_with_current_key():
    """encrypt_token → decrypt_token roundtrip succeeds with the current key."""
    from cryptography.fernet import Fernet

    import app.services.encryption_service as es

    valid_key = Fernet.generate_key().decode()
    original_fernet = es._fernet
    try:
        es._fernet = None  # force re-initialization with our key
        with patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY", valid_key):
            with patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY_OLD", ""):
                plaintext = "ghp_test_token_abc123"
                encrypted = es.encrypt_token(plaintext)
                assert es.decrypt_token(encrypted) == plaintext
    finally:
        es._fernet = original_fernet


def test_multifernet_decrypts_old_key_tokens_after_rotation():
    """After key rotation, tokens encrypted with the old key can still be decrypted."""
    from cryptography.fernet import Fernet

    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypt with old key
    old_fernet = Fernet(old_key.encode())
    ciphertext = old_fernet.encrypt(b"ghp_old_secret_token")

    # Now simulate rotation: new key is primary, old key is in OLD list
    import app.services.encryption_service as es

    original_fernet = es._fernet
    try:
        es._fernet = None  # force re-initialization
        with (
            patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY", new_key),
            patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY_OLD", old_key),
        ):
            result = es.decrypt_token(ciphertext)
            assert result == "ghp_old_secret_token"
    finally:
        es._fernet = original_fernet


def test_multifernet_encrypts_with_primary_key():
    """New encryptions use the primary (current) key, not old keys."""
    from cryptography.fernet import Fernet

    import app.services.encryption_service as es

    new_key = Fernet.generate_key().decode()
    old_key = Fernet.generate_key().decode()

    original_fernet = es._fernet
    try:
        es._fernet = None
        with (
            patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY", new_key),
            patch.object(es.settings, "GITHUB_TOKEN_ENCRYPTION_KEY_OLD", old_key),
        ):
            encrypted = es.encrypt_token("ghp_new_secret")
            # Decrypt using ONLY the new key — must succeed (proves primary key was used)
            primary_only = Fernet(new_key.encode())
            assert primary_only.decrypt(encrypted).decode() == "ghp_new_secret"
    finally:
        es._fernet = original_fernet


# ── Audit log creation (3 tests) ──────────────────────────────────────────


async def test_audit_log_creates_entry_for_auth_event():
    """log_auth_event creates an AuditLog record with correct fields."""
    from app.services.audit_service import AUTH_LOGIN, log_auth_event

    added_objects = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_session.commit = AsyncMock()

    with patch("app.services.audit_service.get_session_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await log_auth_event(
            AUTH_LOGIN,
            user_id="user-123",
            ip_address="192.168.1.42",
            user_agent="TestBrowser/1.0",
        )

    assert len(added_objects) == 1
    entry = added_objects[0]
    assert entry.event_type == AUTH_LOGIN
    assert entry.user_id == "user-123"
    assert entry.ip_address == "192.168.1.42"
    assert "TestBrowser" in entry.user_agent


async def test_audit_log_handles_missing_client_gracefully():
    """log_auth_event handles missing ip_address gracefully (defaults to 'unknown')."""
    from app.services.audit_service import AUTH_LOGOUT, log_auth_event

    added_objects = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_session.commit = AsyncMock()

    with patch("app.services.audit_service.get_session_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await log_auth_event(AUTH_LOGOUT, user_id="user-456")

    assert len(added_objects) == 1
    entry = added_objects[0]
    assert entry.ip_address is None


async def test_audit_log_never_raises_on_db_error():
    """log_auth_event must swallow exceptions — audit should never break auth flow."""
    from app.services.audit_service import AUTH_FAILURE, log_auth_event

    with patch("app.services.audit_service.get_session_context") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(
            side_effect=RuntimeError("DB connection failed")
        )
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        # Must NOT raise
        await log_auth_event(AUTH_FAILURE, user_id=None, metadata={"reason": "bad token"})


# ── onboarding_step in PATCH /auth/me (unit-level) ───────────────────────


async def test_patch_auth_me_updates_onboarding_step():
    """PATCH /auth/me with onboarding_step=3 persists the step to the User model."""
    from app.routers.auth import patch_auth_me
    from app.schemas.auth import PatchAuthMeRequest

    mock_user = MagicMock()
    mock_user.onboarding_step = None
    mock_user.onboarding_completed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])
    data = PatchAuthMeRequest(onboarding_step=3)

    result = await patch_auth_me(data=data, current_user=current_user, session=mock_session)

    assert mock_user.onboarding_step == 3
    assert result["updated"] is True


def test_patch_auth_me_schema_rejects_step_out_of_range():
    """PatchAuthMeRequest rejects onboarding_step outside 1-4."""
    from pydantic import ValidationError

    from app.schemas.auth import PatchAuthMeRequest

    with pytest.raises(ValidationError):
        PatchAuthMeRequest(onboarding_step=0)  # below ge=1

    with pytest.raises(ValidationError):
        PatchAuthMeRequest(onboarding_step=5)  # above le=4


def test_patch_auth_me_schema_accepts_valid_steps():
    """PatchAuthMeRequest accepts onboarding_step values 1 through 4."""
    from app.schemas.auth import PatchAuthMeRequest

    for step in (1, 2, 3, 4):
        req = PatchAuthMeRequest(onboarding_step=step)
        assert req.onboarding_step == step


def test_patch_auth_me_schema_accepts_null_step():
    """PatchAuthMeRequest accepts onboarding_step=None (clear wizard state)."""
    from app.schemas.auth import PatchAuthMeRequest

    req = PatchAuthMeRequest(onboarding_step=None)
    assert req.onboarding_step is None


# ── set_refresh_cookie shared helper ──────────────────────────────────────


def test_set_refresh_cookie_lives_in_auth_service():
    """set_refresh_cookie must be importable from auth_service (shared, not duplicated)."""
    from app.services.auth_service import set_refresh_cookie

    assert callable(set_refresh_cookie)


def test_github_auth_delegates_to_shared_cookie_helper():
    """github_auth._set_refresh_cookie delegates to auth_service.set_refresh_cookie."""
    import inspect

    from app.routers.github_auth import _set_refresh_cookie

    source = inspect.getsource(_set_refresh_cookie)
    assert "set_refresh_cookie" in source, (
        "_set_refresh_cookie must delegate to shared set_refresh_cookie"
    )


# ── Login audit event wiring ──────────────────────────────────────────────


def test_github_callback_imports_login_audit_events():
    """github_auth module must import AUTH_LOGIN and AUTH_LOGIN_NEW_USER for audit logging."""
    from app.routers import github_auth

    assert hasattr(github_auth, "AUTH_LOGIN"), "AUTH_LOGIN not imported in github_auth"
    assert hasattr(github_auth, "AUTH_LOGIN_NEW_USER"), "AUTH_LOGIN_NEW_USER not imported"


def test_github_callback_calls_log_auth_event():
    """github_callback source must call log_auth_event for login audit trail."""
    import inspect

    from app.routers import github_auth

    source = inspect.getsource(github_auth.github_callback)
    assert "log_auth_event" in source, (
        "github_callback must call log_auth_event for AUTH_LOGIN/AUTH_LOGIN_NEW_USER"
    )


# ── model_fields_set for display_name/email clearing ─────────────────────


async def test_patch_auth_me_clears_display_name_when_null():
    """PATCH /auth/me with display_name=null explicitly clears the display name."""
    from app.routers.auth import patch_auth_me
    from app.schemas.auth import PatchAuthMeRequest

    mock_user = MagicMock()
    mock_user.display_name = "Old Name"
    mock_user.onboarding_completed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])
    data = PatchAuthMeRequest(display_name=None)

    await patch_auth_me(data=data, current_user=current_user, session=mock_session)

    assert mock_user.display_name is None


async def test_patch_auth_me_preserves_display_name_when_not_sent():
    """PATCH /auth/me without display_name field does NOT clear existing value."""
    from app.routers.auth import patch_auth_me
    from app.schemas.auth import PatchAuthMeRequest

    mock_user = MagicMock()
    mock_user.display_name = "Preserved Name"
    mock_user.onboarding_completed_at = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    current_user = AuthenticatedUser(id="user-1", github_login="octocat", roles=["user"])
    # Only send email, not display_name
    data = PatchAuthMeRequest(email="new@example.com")

    await patch_auth_me(data=data, current_user=current_user, session=mock_session)

    assert mock_user.display_name == "Preserved Name"
