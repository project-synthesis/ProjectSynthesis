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
VALID_EFFORTS: set[str] = {"low", "medium", "high", "max"}


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
        "enable_llm_classification_fallback": True,
        "force_sampling": False,
        "force_passthrough": False,
        "optimizer_effort": "high",
        "analyzer_effort": "low",
        "scorer_effort": "low",
    },
    "defaults": {
        "strategy": "auto",
    },
    "phase_weights": {
        "analysis": {"w_topic": 0.60, "w_transform": 0.15, "w_output": 0.10, "w_pattern": 0.15},
        "optimization": {"w_topic": 0.20, "w_transform": 0.35, "w_output": 0.25, "w_pattern": 0.20},
        "pattern_injection": {"w_topic": 0.25, "w_transform": 0.25, "w_output": 0.20, "w_pattern": 0.30},
        "scoring": {"w_topic": 0.15, "w_transform": 0.20, "w_output": 0.45, "w_pattern": 0.20},
    },
    "domain_readiness_notifications": {
        # Default ON: PR #27 follow-up. Feature was unreachable when False
        # because no global UI toggle shipped. Users opt out via the master
        # bell in DomainReadinessPanel or per-row mutes.
        "enabled": True,
        "muted_domain_ids": [],
    },
}

_PIPELINE_TOGGLES = (
    "enable_explore", "enable_scoring", "enable_adaptation",
    "enable_llm_classification_fallback",
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

        # Effort keys must be in VALID_EFFORTS
        for effort_key in ("optimizer_effort", "analyzer_effort", "scorer_effort"):
            effort = pipeline.get(effort_key)
            if effort not in VALID_EFFORTS:
                default_effort = DEFAULTS["pipeline"][effort_key]
                logger.warning(
                    "Invalid %s '%s' — falling back to '%s'",
                    effort_key, effort, default_effort,
                )
                pipeline[effort_key] = default_effort

        # Domain readiness notifications section must be a dict with
        # a bool `enabled` and a list[str] `muted_domain_ids`.
        notif_default = DEFAULTS["domain_readiness_notifications"]
        notif = prefs.get("domain_readiness_notifications")
        if not isinstance(notif, dict):
            logger.warning(
                "Invalid domain_readiness_notifications %r — falling back to defaults",
                notif,
            )
            prefs["domain_readiness_notifications"] = copy.deepcopy(notif_default)
        else:
            enabled = notif.get("enabled")
            if not isinstance(enabled, bool):
                logger.warning(
                    "Invalid domain_readiness_notifications.enabled %r — falling back to %s",
                    enabled, notif_default["enabled"],
                )
                notif["enabled"] = notif_default["enabled"]
            muted = notif.get("muted_domain_ids")
            if not (isinstance(muted, list) and all(isinstance(x, str) for x in muted)):
                logger.warning(
                    "Invalid domain_readiness_notifications.muted_domain_ids %r — "
                    "falling back to %s",
                    muted, notif_default["muted_domain_ids"],
                )
                notif["muted_domain_ids"] = copy.deepcopy(notif_default["muted_domain_ids"])

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

        for effort_key in ("optimizer_effort", "analyzer_effort", "scorer_effort"):
            effort = pipeline.get(effort_key)
            if effort is not None and effort not in VALID_EFFORTS:
                raise ValueError(
                    f"Invalid {effort_key} '{effort}'. Valid: {sorted(VALID_EFFORTS)}"
                )

        if pipeline.get("force_sampling") and pipeline.get("force_passthrough"):
            raise ValueError("force_sampling and force_passthrough are mutually exclusive")

        notif = prefs.get("domain_readiness_notifications")
        if notif is not None:
            if not isinstance(notif, dict):
                raise ValueError(
                    "Invalid domain_readiness_notifications: expected dict, "
                    f"got {type(notif).__name__}"
                )
            enabled = notif.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                raise ValueError(
                    "Invalid domain_readiness_notifications.enabled: expected bool, "
                    f"got {type(enabled).__name__}"
                )
            muted = notif.get("muted_domain_ids")
            if muted is not None:
                if not isinstance(muted, list):
                    raise ValueError(
                        "Invalid domain_readiness_notifications.muted_domain_ids: "
                        f"expected list, got {type(muted).__name__}"
                    )
                if not all(isinstance(x, str) for x in muted):
                    raise ValueError(
                        "Invalid domain_readiness_notifications.muted_domain_ids: "
                        "all entries must be strings"
                    )

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
