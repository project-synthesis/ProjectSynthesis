"""File-based JSON preferences with validation and atomic writes.

Preferences are stored at ``data/preferences.json``.  The service provides
load/save/patch/resolve_model with a snapshot pattern — ``load()`` returns a
frozen-copy dict so callers can pass a consistent snapshot through the
pipeline without mid-flight mutations.
"""

import copy
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from app.config import PROMPTS_DIR, settings

logger = logging.getLogger(__name__)

VALID_MODELS: set[str] = {"sonnet", "opus", "haiku"}


def _discover_strategies() -> set[str] | None:
    """Discover available strategies from disk (prompts/strategies/*.md).

    Returns None if no strategies directory or no files — meaning
    validation should be skipped (accept any value).
    """
    strategies_dir = PROMPTS_DIR / "strategies"
    if not strategies_dir.is_dir():
        return None
    found = {p.stem for p in strategies_dir.glob("*.md")}
    return found if found else None

DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "models": {
        "analyzer": "sonnet",
        "optimizer": "opus",
        "scorer": "sonnet",
    },
    "pipeline": {
        "enable_explore": True,
        "enable_scoring": True,
        "enable_adaptation": True,
        "force_sampling": False,
        "force_passthrough": False,
    },
    "defaults": {
        "strategy": "auto",
    },
}

_PIPELINE_TOGGLES = (
    "enable_explore", "enable_scoring", "enable_adaptation",
    "force_sampling", "force_passthrough",
)

_MODEL_MAP = {
    "sonnet": "MODEL_SONNET",
    "opus": "MODEL_OPUS",
    "haiku": "MODEL_HAIKU",
}


class PreferencesService:
    """Manage user preferences persisted as a JSON file."""

    def __init__(self, data_dir: Path | None = None) -> None:
        from app.config import DATA_DIR

        self._data_dir = data_dir or DATA_DIR
        self._path = self._data_dir / "preferences.json"

    # ── public API ───────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Read preferences from disk, deep-merge with defaults, sanitize.

        Creates the file with defaults if it does not exist or contains
        invalid JSON.  Always returns a *fresh* dict snapshot.
        """
        disk: dict[str, Any] = {}
        if self._path.exists():
            try:
                disk = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupt preferences file — resetting to defaults: %s", exc)
                disk = {}

        merged = self._deep_merge(copy.deepcopy(DEFAULTS), disk)
        self._sanitize(merged)
        self._write(merged)
        return copy.deepcopy(merged)

    def save(self, prefs: dict[str, Any]) -> None:
        """Validate then atomically persist *prefs*."""
        self._validate(prefs)
        self._write(prefs)

    def patch(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Load current prefs, deep-merge *updates*, validate, save, return."""
        current = self.load()
        merged = self._deep_merge(current, updates)
        self._validate(merged)
        self._write(merged)
        return copy.deepcopy(merged)

    def get(self, path: str, snapshot: dict[str, Any] | None = None) -> Any:
        """Dot-path accessor (e.g. ``"models.analyzer"``).

        Uses *snapshot* if provided, otherwise reads from disk.
        Returns ``None`` for missing paths.
        """
        data = snapshot if snapshot is not None else self.load()
        parts = path.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def resolve_model(self, phase: str, snapshot: dict[str, Any] | None = None) -> str:
        """Map a phase name (analyzer/optimizer/scorer) to a full model ID.

        Uses the preferences snapshot or reads from disk.
        """
        data = snapshot if snapshot is not None else self.load()
        short = data.get("models", {}).get(phase, "sonnet")
        attr = _MODEL_MAP.get(short, "MODEL_SONNET")
        return getattr(settings, attr)

    # ── internals ────────────────────────────────────────────────

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Recursively merge *override* into *base* (mutates and returns *base*)."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                PreferencesService._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    @staticmethod
    def _sanitize(prefs: dict[str, Any]) -> None:
        """Replace invalid model/strategy values with defaults, log replacements."""
        models = prefs.get("models", {})
        for role, default_val in DEFAULTS["models"].items():
            val = models.get(role)
            if val not in VALID_MODELS:
                logger.warning(
                    "Invalid model '%s' for %s — falling back to '%s'", val, role, default_val
                )
                models[role] = default_val

        strategy = prefs.get("defaults", {}).get("strategy")
        valid_strategies = _discover_strategies()
        # Only validate if strategies exist on disk; skip if directory is empty
        if valid_strategies is not None and strategy not in valid_strategies:
            default_strategy = DEFAULTS["defaults"]["strategy"]
            logger.warning(
                "Invalid strategy '%s' — falling back to '%s'", strategy, default_strategy
            )
            prefs.setdefault("defaults", {})["strategy"] = default_strategy

        # Pipeline toggles must be boolean
        pipeline = prefs.get("pipeline", {})
        for toggle in _PIPELINE_TOGGLES:
            val = pipeline.get(toggle)
            if not isinstance(val, bool):
                default_val = DEFAULTS["pipeline"][toggle]
                logger.warning(
                    "Non-boolean pipeline toggle '%s'=%r — falling back to %s",
                    toggle, val, default_val,
                )
                pipeline[toggle] = default_val

    @staticmethod
    def _validate(prefs: dict[str, Any]) -> None:
        """Raise ``ValueError`` for invalid model, strategy, or toggle values."""
        models = prefs.get("models", {})
        for role in ("analyzer", "optimizer", "scorer"):
            val = models.get(role)
            if val is not None and val not in VALID_MODELS:
                raise ValueError(
                    f"Invalid model '{val}' for {role}. Valid: {sorted(VALID_MODELS)}"
                )

        strategy = prefs.get("defaults", {}).get("strategy")
        if strategy is not None:
            valid_strategies = _discover_strategies()
            # Only reject if strategies exist on disk and the value isn't among them
            if valid_strategies is not None and strategy not in valid_strategies:
                raise ValueError(
                    f"Invalid strategy '{strategy}'. Valid: {sorted(valid_strategies)}"
                )

        pipeline = prefs.get("pipeline", {})
        for toggle in _PIPELINE_TOGGLES:
            val = pipeline.get(toggle)
            if val is not None and not isinstance(val, bool):
                raise ValueError(
                    f"Pipeline toggle '{toggle}' must be boolean, got {type(val).__name__}"
                )

        if pipeline.get("force_sampling") and pipeline.get("force_passthrough"):
            raise ValueError("force_sampling and force_passthrough are mutually exclusive")

    def _write(self, prefs: dict[str, Any]) -> None:
        """Atomic write via tempfile + rename, mode 0o644."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._data_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(prefs, f, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, self._path)
        except BaseException:
            # Clean up temp file on any error
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
