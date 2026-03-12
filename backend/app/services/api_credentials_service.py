"""Anthropic API key encrypted persistence and hot-reload.

Modeled after ``github_credentials_service.py`` but uses Fernet encryption
via the shared ``github_service.encrypt_token / decrypt_token`` infrastructure.

The API key is stored Fernet-encrypted at ``data/.api_credentials`` with 0o600
permissions.  At startup ``load_api_key_from_file()`` decrypts and applies the
key to the settings singleton (only when no env-var override is present).

The ``save_api_key()`` function encrypts, persists, and hot-reloads the settings
singleton so the provider can be re-detected without a restart.
"""

import logging
import os
import tempfile
from pathlib import Path

from app.config import settings
from app.services.github_service import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

_CREDS_FILE = Path("data/.api_credentials")

# B5: Module-level flag for credential load errors
_credential_load_error: str | None = None


def load_api_key_from_file() -> None:
    """Load a saved API key and apply to settings singleton.

    Only applies the saved key when ``ANTHROPIC_API_KEY`` is empty (not set
    via environment variable).  Called once at startup before provider detection.
    """
    if settings.ANTHROPIC_API_KEY:
        logger.debug("ANTHROPIC_API_KEY set via environment — skipping file load")
        return

    if not _CREDS_FILE.exists():
        return

    try:
        encrypted = _CREDS_FILE.read_bytes()
        api_key = decrypt_token(encrypted)
        if api_key:
            settings.ANTHROPIC_API_KEY = api_key
            logger.info("Anthropic API key loaded from %s", _CREDS_FILE)
    except Exception as e:
        global _credential_load_error
        _credential_load_error = f"{type(e).__name__}: {e}"
        logger.warning("Failed to load API key from %s: %s", _CREDS_FILE, e)


def save_api_key(api_key: str) -> None:
    """Encrypt and persist an API key, then hot-reload the settings singleton.

    Uses atomic write (temp file + os.replace) with 0o600 permissions.
    """
    _CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)

    encrypted = encrypt_token(api_key)

    # Atomic write with restricted permissions
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=_CREDS_FILE.parent, prefix=".tmp_api_creds_"
    )
    try:
        os.write(tmp_fd, encrypted)
        os.close(tmp_fd)
        # Set permissions before replacing to avoid a window of insecure perms
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, _CREDS_FILE)
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # Hot-reload and clear any prior credential load error
    global _credential_load_error
    settings.ANTHROPIC_API_KEY = api_key
    _credential_load_error = None
    logger.info("Anthropic API key saved and hot-reloaded")


def delete_api_key() -> None:
    """Remove the saved API key file and clear settings if it was file-sourced."""
    if _CREDS_FILE.exists():
        _CREDS_FILE.unlink()
        logger.info("Anthropic API key file deleted")

    # Only clear if the current key came from the file (not env var).
    # We check by seeing if _CREDS_FILE no longer exists — if it was from env,
    # the env var is still set and we shouldn't touch it.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        settings.ANTHROPIC_API_KEY = ""
        logger.info("Anthropic API key cleared from settings")


def get_api_key_status() -> dict:
    """Return masked API key status for API responses.

    Returns:
        Dict with configured, source, and masked fields.
    """
    key = settings.ANTHROPIC_API_KEY
    if not key:
        return {"configured": False, "source": "none", "masked": ""}

    # Determine source
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if env_key and env_key == key:
        source = "environment"
    elif _CREDS_FILE.exists():
        source = "app"
    else:
        source = "environment"  # set somehow but no file — assume env

    # Mask: show prefix + last 4 chars
    if len(key) > 12:
        masked = key[:7] + "..." + key[-4:]
    elif len(key) > 4:
        masked = key[:3] + "..." + key[-2:]
    else:
        masked = "****"

    return {"configured": True, "source": source, "masked": masked}


def get_credential_load_error() -> str | None:
    """Return the credential load error message, if any."""
    return _credential_load_error
