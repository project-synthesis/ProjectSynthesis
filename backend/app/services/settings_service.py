"""Application settings persistence.

Provides load/save for application-level settings stored in a JSON file.
Extracted from the settings router so that services (e.g. pipeline) can
read settings without violating the layer rule (services → routers is forbidden).
"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

SETTINGS_FILE = os.path.join("data", "app_settings.json")

# Default application settings
DEFAULT_SETTINGS: dict = {
    "default_model": "auto",
    "pipeline_timeout": 300,
    "max_retries": 1,
    "default_strategy": None,
    "auto_validate": True,
    "stream_optimize": True,
}


def load_settings() -> dict:
    """Load settings from the JSON file, falling back to defaults.

    Returns:
        Dict of current settings values.
    """
    settings = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                stored = json.load(f)
            settings.update(stored)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load settings file: %s", e)
    return settings


def save_settings(settings_data: dict) -> None:
    """Persist settings to the JSON file.

    Args:
        settings_data: Dict of settings values to persist.

    Raises:
        OSError: If the file cannot be written.
    """
    dir_path = os.path.dirname(SETTINGS_FILE)
    os.makedirs(dir_path, exist_ok=True)
    # Atomic write: temp file + rename prevents corruption on crash
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(settings_data, f, indent=2)
        os.replace(tmp_path, SETTINGS_FILE)
    except BaseException:
        # Clean up temp file on any failure (including KeyboardInterrupt)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
