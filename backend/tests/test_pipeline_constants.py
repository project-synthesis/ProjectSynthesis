"""Tests for pipeline_constants — A3 analyze-effort ceiling + related helpers."""

from __future__ import annotations

import pytest

from app.services import pipeline_constants as pc


class TestClampAnalyzeEffort:
    """A3: the analyze phase is a classification task — even `high` is
    generous. Deep thinking at `max` burns 200+s of sonnet-4-6 thinking
    tokens on a 50-output-token structured response. The ceiling clamps
    any preference above `high` down to `high`; below `high` passes
    through untouched. `low` stays the default for unknown/missing input.
    """

    def test_max_clamps_to_high(self):
        assert pc.clamp_analyze_effort("max") == "high"

    def test_xhigh_clamps_to_high(self):
        assert pc.clamp_analyze_effort("xhigh") == "high"

    def test_high_passes_through(self):
        assert pc.clamp_analyze_effort("high") == "high"

    def test_medium_passes_through(self):
        assert pc.clamp_analyze_effort("medium") == "medium"

    def test_low_passes_through(self):
        assert pc.clamp_analyze_effort("low") == "low"

    def test_none_defaults_to_low(self):
        assert pc.clamp_analyze_effort(None) == "low"

    def test_empty_string_defaults_to_low(self):
        assert pc.clamp_analyze_effort("") == "low"

    def test_unknown_value_defaults_to_low(self):
        """Guards against preference-file typos like `"maximum"`."""
        assert pc.clamp_analyze_effort("maximum") == "low"

    def test_case_insensitive(self):
        assert pc.clamp_analyze_effort("MAX") == "high"
        assert pc.clamp_analyze_effort("High") == "high"

    def test_ceiling_constant_is_high(self):
        """The ceiling is the public contract — documented in the code review
        that landed A3. Anchor the value so a silent bump to `max` is caught."""
        assert pc.ANALYZE_EFFORT_CEILING == "high"
