"""Tests for pipeline_constants — A3 analyze-effort ceiling + related helpers."""

from __future__ import annotations

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


class TestShouldRunStrategyIntelligenceFallback:
    """A9: pipeline.py's fallback re-fetch of strategy_intelligence must honor
    the enrichment profile. Live audit (2026-04-21) caught cold_start runs
    logging ``strategy_intel=none`` at enrichment but ``strategy_intel=82``
    at optimize — the fallback was silently defeating the profile's skip.

    The gate centralizes the three conditions the pipeline checks (feature
    enabled, not already populated, profile allows SI) so the logic is
    exhaustively testable and both pipelines (REST + sampling) stay in sync.
    """

    def test_skips_when_cold_start_profile(self):
        """The profile skip is intentional — < 10 opts means no meaningful
        adaptation signal yet, so re-fetching burns a DB round-trip for noise."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence=None,
            enrichment_profile="cold_start",
            enable_strategy_intelligence=True,
        ) is False

    def test_runs_when_knowledge_work_profile(self):
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence=None,
            enrichment_profile="knowledge_work",
            enable_strategy_intelligence=True,
        ) is True

    def test_runs_when_code_aware_profile(self):
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence=None,
            enrichment_profile="code_aware",
            enable_strategy_intelligence=True,
        ) is True

    def test_skips_when_already_populated(self):
        """Enrichment already handed us SI — re-fetching would overwrite it."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence="existing intel text",
            enrichment_profile="code_aware",
            enable_strategy_intelligence=True,
        ) is False

    def test_skips_when_feature_disabled(self):
        """Preference `enable_strategy_intelligence=False` is an explicit kill
        switch — the fallback must not silently resurrect the feature."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence=None,
            enrichment_profile="code_aware",
            enable_strategy_intelligence=False,
        ) is False

    def test_runs_when_profile_missing(self):
        """Legacy/test callers that don't set a profile get the old behavior
        (re-fetch allowed). Only the explicit ``cold_start`` string blocks."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence=None,
            enrichment_profile=None,
            enable_strategy_intelligence=True,
        ) is True

    def test_populated_wins_over_disabled(self):
        """Edge: if enrichment populated SI but preference is now disabled,
        the fallback still shouldn't fire — nothing to fetch anyway."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence="existing",
            enrichment_profile="code_aware",
            enable_strategy_intelligence=False,
        ) is False

    def test_populated_wins_over_cold_start(self):
        """Edge: enrichment managed to populate SI despite cold_start (e.g.
        profile was overridden upstream). Don't re-fetch over existing value."""
        assert pc.should_run_strategy_intelligence_fallback(
            strategy_intelligence="existing",
            enrichment_profile="cold_start",
            enable_strategy_intelligence=True,
        ) is False
