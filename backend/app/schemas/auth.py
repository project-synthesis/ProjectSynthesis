"""JWT authentication schemas and error code constants."""
from __future__ import annotations

from pydantic import BaseModel, Field


class AuthenticatedUser(BaseModel):
    id: str
    github_login: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PatchAuthMeRequest(BaseModel):
    """Request body for PATCH /auth/me.

    All fields are optional — only supplied fields are updated.
    """
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    onboarding_completed: bool | None = Field(
        default=None,
        description="When True, stamps onboarding_completed_at = now(). "
                    "When False, clears the timestamp (resets onboarding).",
    )


class GetAuthMeResponse(BaseModel):
    """Response body for GET /auth/me."""
    id: str
    github_login: str
    github_user_id: int
    role: str
    email: str | None
    avatar_url: str | None
    display_name: str | None
    onboarding_completed: bool
    onboarding_completed_at: str | None
    last_login_at: str | None
    created_at: str


class SessionsResponse(BaseModel):
    """Response body for DELETE /auth/sessions."""
    revoked_sessions: int


# Error code constants — used in HTTPException detail dicts
ERR_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
ERR_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
ERR_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
ERR_TOKEN_REVOKED = "AUTH_TOKEN_REVOKED"
ERR_INSUFFICIENT_PERMISSIONS = "AUTH_INSUFFICIENT_PERMISSIONS"
