"""GitHub token encryption/decryption (Fernet) and OAuth URL building."""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class GitHubService:
    """Handles GitHub OAuth token encryption and URL construction."""

    def __init__(self, secret_key: str, client_id: str = "", client_secret: str = "") -> None:
        # Derive a Fernet key from the app's secret key
        key = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))
        self._client_id = client_id
        self._client_secret = client_secret

    def encrypt_token(self, token: str) -> bytes:
        return self._fernet.encrypt(token.encode())

    def decrypt_token(self, encrypted: bytes) -> str:
        return self._fernet.decrypt(encrypted).decode()

    def build_oauth_url(self, state: str, scope: str = "repo,read:user") -> str:
        return (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self._client_id}"
            f"&scope={scope}"
            f"&state={state}"
        )
