"""GitHub token encryption/decryption (Fernet) and OAuth URL building."""

import logging
from collections.abc import Callable

from app.utils.crypto import decrypt_with_migration, derive_fernet

logger = logging.getLogger(__name__)

_GITHUB_TOKEN_CONTEXT = "synthesis-github-token-v1"


class GitHubService:
    """Handles GitHub OAuth token encryption and URL construction."""

    def __init__(self, secret_key: str, client_id: str = "", client_secret: str = "") -> None:
        self._secret_key = secret_key
        self._fernet = derive_fernet(secret_key, _GITHUB_TOKEN_CONTEXT)
        self._client_id = client_id
        self._client_secret = client_secret

    def encrypt_token(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt_token(self, encrypted: bytes, persist_fn: Callable[[bytes], None] | None = None) -> str:
        """Decrypt a GitHub token, migrating from legacy KDF if needed."""
        return decrypt_with_migration(
            encrypted,
            self._secret_key,
            _GITHUB_TOKEN_CONTEXT,
            persist_fn=persist_fn,
        ).decode()

    def build_oauth_url(self, state: str, scope: str = "repo,read:user") -> str:
        return (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self._client_id}"
            f"&scope={scope}"
            f"&state={state}"
        )
