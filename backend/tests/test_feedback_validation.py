"""Tests for feedback service-layer validation functions."""

import pytest

from app.services.feedback_service import (
    validate_corrected_issues,
    validate_dimension_overrides,
)


class TestValidateDimensionOverrides:
    def test_valid_overrides_pass(self):
        result = validate_dimension_overrides({"clarity_score": 8})
        assert result == {"clarity_score": 8}

    def test_multiple_valid_overrides(self):
        overrides = {"clarity_score": 8, "specificity_score": 7}
        result = validate_dimension_overrides(overrides)
        assert result == overrides

    def test_invalid_dimension_key_rejected(self):
        with pytest.raises(ValueError, match="Invalid dimension"):
            validate_dimension_overrides({"invalid_dim": 5})

    def test_out_of_range_value_rejected_high(self):
        with pytest.raises(ValueError, match="Score must be 1-10"):
            validate_dimension_overrides({"clarity_score": 11})

    def test_out_of_range_value_rejected_low(self):
        with pytest.raises(ValueError, match="Score must be 1-10"):
            validate_dimension_overrides({"clarity_score": 0})

    def test_none_overrides_pass(self):
        result = validate_dimension_overrides(None)
        assert result is None

    def test_empty_overrides_pass(self):
        result = validate_dimension_overrides({})
        assert result == {}

    def test_boundary_values_pass(self):
        result = validate_dimension_overrides({"clarity_score": 1})
        assert result == {"clarity_score": 1}
        result = validate_dimension_overrides({"clarity_score": 10})
        assert result == {"clarity_score": 10}


class TestValidateCorrectedIssues:
    def test_valid_issues_pass(self):
        result = validate_corrected_issues(["lost_key_terms", "too_verbose"])
        assert result == ["lost_key_terms", "too_verbose"]

    def test_invalid_issue_rejected(self):
        with pytest.raises(ValueError, match="Invalid issue"):
            validate_corrected_issues(["nonexistent"])

    def test_issues_deduplicated(self):
        result = validate_corrected_issues(["lost_key_terms", "lost_key_terms"])
        assert result == ["lost_key_terms"]

    def test_none_issues_pass(self):
        result = validate_corrected_issues(None)
        assert result is None

    def test_empty_list_pass(self):
        result = validate_corrected_issues([])
        assert result == []

    def test_order_preserved_after_dedup(self):
        result = validate_corrected_issues([
            "too_verbose", "lost_key_terms", "too_verbose", "wrong_tone",
        ])
        assert result == ["too_verbose", "lost_key_terms", "wrong_tone"]
