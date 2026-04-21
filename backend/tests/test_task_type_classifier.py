"""Tests for the task_type_classifier signal-extraction state (A4)."""

from __future__ import annotations

import pytest

from app.services import task_type_classifier as ttc


@pytest.fixture(autouse=True)
def _reset_extraction_state():
    """Isolate each test — the extraction set is a module-level singleton."""
    ttc.reset_task_type_extracted()
    yield
    ttc.reset_task_type_extracted()


class TestTaskTypeHasDynamicSignals:
    """A4: extraction state must be tracked explicitly, not inferred from the
    merged signal table. Signals loaded from a cache at boot look identical
    to signals just extracted in the current run, but only the latter prove
    live TF-IDF learning has fired.
    """

    def test_default_state_is_bootstrap_for_all_task_types(self):
        assert ttc.task_type_has_dynamic_signals("coding") is False
        assert ttc.task_type_has_dynamic_signals("writing") is False
        assert ttc.task_type_has_dynamic_signals("analysis") is False

    def test_set_task_type_signals_without_extracted_set_stays_bootstrap(self):
        """Loading singles without naming which types actually crossed the
        30-sample threshold does NOT mark them as dynamic. Calls from
        cache-warmup at boot must go through this path."""
        ttc.set_task_type_signals({"coding": [("endpoint", 0.9)]})
        assert ttc.task_type_has_dynamic_signals("coding") is False

    def test_set_task_type_signals_with_extracted_marks_dynamic(self):
        """Passing ``extracted_task_types`` flips those types to dynamic."""
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)], "writing": [("blog", 0.8)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("coding") is True
        assert ttc.task_type_has_dynamic_signals("writing") is False

    def test_subsequent_bootstrap_load_clears_dynamic_status(self):
        """A later cache-warmup load (no extracted set) must not preserve the
        dynamic status from an earlier extraction run — dynamic is a claim
        about *this run's* signal freshness, not an eternal attribute."""
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("coding") is True

        ttc.set_task_type_signals({"coding": [("endpoint", 0.9)]})
        assert ttc.task_type_has_dynamic_signals("coding") is False

    def test_unknown_task_type_returns_false(self):
        ttc.set_task_type_signals(
            {"coding": [("endpoint", 0.9)]},
            extracted_task_types={"coding"},
        )
        assert ttc.task_type_has_dynamic_signals("meta-coding") is False
