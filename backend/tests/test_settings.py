"""Tests for settings router validation.

S-val  — SettingsUpdate Pydantic validation
S-svc  — settings_service load/save behaviour
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers.settings import SettingsUpdate

# ---------------------------------------------------------------------------
# S-val-1: unknown strategy rejected by field_validator
# ---------------------------------------------------------------------------

def test_settings_rejects_unknown_strategy():
    """SettingsUpdate must reject strategy values not in KNOWN_FRAMEWORKS."""
    with pytest.raises(ValidationError, match="Unknown strategy"):
        SettingsUpdate(default_strategy="nonexistent-framework")


def test_settings_accepts_known_strategy():
    """SettingsUpdate must accept all frameworks from KNOWN_FRAMEWORKS."""
    from app.services.strategy_selector import KNOWN_FRAMEWORKS

    for fw in sorted(KNOWN_FRAMEWORKS):
        update = SettingsUpdate(default_strategy=fw)
        assert update.default_strategy == fw


def test_settings_accepts_null_strategy():
    """SettingsUpdate must accept None for default_strategy (auto mode)."""
    update = SettingsUpdate(default_strategy=None)
    assert update.default_strategy is None


# ---------------------------------------------------------------------------
# S-val-2: pipeline_timeout range validation
# ---------------------------------------------------------------------------

def test_settings_rejects_timeout_below_minimum():
    """pipeline_timeout below 10 must be rejected."""
    with pytest.raises(ValidationError, match="greater than or equal to 10"):
        SettingsUpdate(pipeline_timeout=5)


def test_settings_rejects_timeout_above_maximum():
    """pipeline_timeout above 600 must be rejected."""
    with pytest.raises(ValidationError, match="less than or equal to 600"):
        SettingsUpdate(pipeline_timeout=999)


def test_settings_accepts_valid_timeout():
    """pipeline_timeout within bounds must be accepted."""
    update = SettingsUpdate(pipeline_timeout=120)
    assert update.pipeline_timeout == 120


# ---------------------------------------------------------------------------
# S-val-3: max_retries range validation
# ---------------------------------------------------------------------------

def test_settings_rejects_negative_retries():
    """max_retries below 0 must be rejected."""
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        SettingsUpdate(max_retries=-1)


def test_settings_rejects_retries_above_maximum():
    """max_retries above 5 must be rejected."""
    with pytest.raises(ValidationError, match="less than or equal to 5"):
        SettingsUpdate(max_retries=10)


# ---------------------------------------------------------------------------
# S-val-4: exclude_unset preserves explicit null
# ---------------------------------------------------------------------------

def test_settings_update_exclude_unset_preserves_null():
    """model_dump(exclude_unset=True) must include explicit null values
    so that the PATCH endpoint can distinguish 'clear this field' from
    'leave unchanged'."""
    update = SettingsUpdate(default_strategy=None)
    dump = update.model_dump(exclude_unset=True)
    assert "default_strategy" in dump
    assert dump["default_strategy"] is None


def test_settings_update_exclude_unset_omits_missing():
    """model_dump(exclude_unset=True) must omit fields not in the request."""
    update = SettingsUpdate(max_retries=3)
    dump = update.model_dump(exclude_unset=True)
    assert "max_retries" in dump
    assert "default_model" not in dump
    assert "pipeline_timeout" not in dump


# ---------------------------------------------------------------------------
# S-svc-1: KNOWN_FRAMEWORKS is complete and non-empty
# ---------------------------------------------------------------------------

def test_known_frameworks_nonempty_and_complete():
    """KNOWN_FRAMEWORKS must contain all primary and secondary frameworks."""
    from app.services.strategy_selector import KNOWN_FRAMEWORKS, TASK_FRAMEWORK_MAP

    assert len(KNOWN_FRAMEWORKS) >= 8, "Expected at least 8 unique frameworks"

    # Every primary and secondary in the map must be in KNOWN_FRAMEWORKS
    for primary, secondaries, _ in TASK_FRAMEWORK_MAP.values():
        assert primary in KNOWN_FRAMEWORKS, f"Primary '{primary}' missing"
        for sec in secondaries:
            assert sec in KNOWN_FRAMEWORKS, f"Secondary '{sec}' missing"
