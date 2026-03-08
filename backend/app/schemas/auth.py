"""JWT authentication schemas and error code constants."""
from pydantic import BaseModel


class AuthenticatedUser(BaseModel):
    id: str
    github_login: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# Error code constants — used in HTTPException detail dicts
ERR_TOKEN_MISSING = "AUTH_TOKEN_MISSING"
ERR_TOKEN_INVALID = "AUTH_TOKEN_INVALID"
ERR_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
ERR_TOKEN_REVOKED = "AUTH_TOKEN_REVOKED"
ERR_INSUFFICIENT_PERMISSIONS = "AUTH_INSUFFICIENT_PERMISSIONS"
