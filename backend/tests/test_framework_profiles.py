# backend/tests/test_framework_profiles.py
"""Tests for framework validation profiles — static config."""
from app.services.framework_profiles import (
    CORRECTABLE_ISSUES,
    DEFAULT_FRAMEWORK_PROFILE,
    FRAMEWORK_PROFILES,
    FRAMEWORK_TRADE_OFF_PATTERNS,
    ISSUE_DIMENSION_MAP,
    get_profile,
)
from app.services.prompt_diff import SCORE_DIMENSIONS


class TestFrameworkProfiles:
    def test_all_known_frameworks_have_profiles(self):
        known = {
            "chain-of-thought", "step-by-step", "persona-assignment",
            "CO-STAR", "RISEN", "structured-output", "constraint-injection",
            "few-shot-scaffolding", "context-enrichment", "role-task-format",
        }
        assert known.issubset(set(FRAMEWORK_PROFILES.keys()))

    def test_default_profile_has_neutral_multipliers(self):
        assert DEFAULT_FRAMEWORK_PROFILE["emphasis"] == {}
        assert DEFAULT_FRAMEWORK_PROFILE["de_emphasis"] == {}
        assert DEFAULT_FRAMEWORK_PROFILE["entropy_tolerance"] == 1.0

    def test_get_profile_returns_known_framework(self):
        profile = get_profile("chain-of-thought")
        assert profile["emphasis"]["structure_score"] == 1.3

    def test_get_profile_returns_default_for_unknown(self):
        profile = get_profile("nonexistent-framework")
        assert profile is DEFAULT_FRAMEWORK_PROFILE

    def test_all_emphasis_keys_are_valid_dimensions(self):
        for fw, profile in FRAMEWORK_PROFILES.items():
            for dim in profile.get("emphasis", {}):
                assert dim in SCORE_DIMENSIONS, f"{fw}: {dim} not in SCORE_DIMENSIONS"
            for dim in profile.get("de_emphasis", {}):
                assert dim in SCORE_DIMENSIONS, f"{fw}: {dim} not in SCORE_DIMENSIONS"

    def test_entropy_tolerance_in_valid_range(self):
        for fw, profile in FRAMEWORK_PROFILES.items():
            et = profile["entropy_tolerance"]
            assert 0.5 <= et <= 1.5, f"{fw}: entropy_tolerance {et} out of range"


class TestCorrectableIssues:
    def test_has_eight_issues(self):
        assert len(CORRECTABLE_ISSUES) == 8

    def test_all_issue_ids_are_snake_case(self):
        for issue_id in CORRECTABLE_ISSUES:
            assert issue_id == issue_id.lower().replace(" ", "_")
            assert "-" not in issue_id

    def test_all_issues_mapped_to_dimensions(self):
        for issue_id in CORRECTABLE_ISSUES:
            assert issue_id in ISSUE_DIMENSION_MAP, f"{issue_id} not in ISSUE_DIMENSION_MAP"
            assert len(ISSUE_DIMENSION_MAP[issue_id]) >= 1

    def test_dimension_map_values_are_valid_dimensions(self):
        for issue_id, dim_map in ISSUE_DIMENSION_MAP.items():
            for dim in dim_map:
                assert dim in SCORE_DIMENSIONS, f"{issue_id}: {dim} not valid"

    def test_dimension_map_values_are_positive(self):
        for issue_id, dim_map in ISSUE_DIMENSION_MAP.items():
            for dim, weight in dim_map.items():
                assert weight > 0, f"{issue_id}.{dim} weight must be positive"


class TestTradeOffPatterns:
    def test_patterns_reference_valid_frameworks(self):
        for fw in FRAMEWORK_TRADE_OFF_PATTERNS:
            assert fw in FRAMEWORK_PROFILES, f"{fw} not a known framework"

    def test_patterns_reference_valid_dimensions(self):
        for fw, patterns in FRAMEWORK_TRADE_OFF_PATTERNS.items():
            for gained, lost in patterns:
                assert gained in SCORE_DIMENSIONS, f"{fw}: {gained} invalid"
                assert lost in SCORE_DIMENSIONS, f"{fw}: {lost} invalid"
