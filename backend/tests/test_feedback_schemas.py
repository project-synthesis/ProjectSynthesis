"""Tests for feedback Pydantic schemas — corrected_issues validation."""
import pytest
from pydantic import ValidationError

from app.schemas.feedback import FeedbackCreate


class TestFeedbackCreateSchema:
    def test_valid_corrected_issues(self):
        fb = FeedbackCreate(rating=1, corrected_issues=["lost_key_terms", "too_verbose"])
        assert fb.corrected_issues == ["lost_key_terms", "too_verbose"]

    def test_invalid_corrected_issue_rejected(self):
        with pytest.raises(ValidationError, match="Invalid issue"):
            FeedbackCreate(rating=-1, corrected_issues=["nonexistent_issue"])

    def test_duplicate_corrected_issues_deduplicated(self):
        fb = FeedbackCreate(rating=-1, corrected_issues=["lost_key_terms", "lost_key_terms", "too_verbose"])
        assert fb.corrected_issues == ["lost_key_terms", "too_verbose"]

    def test_null_corrected_issues_allowed(self):
        fb = FeedbackCreate(rating=1)
        assert fb.corrected_issues is None

    def test_empty_corrected_issues_allowed(self):
        fb = FeedbackCreate(rating=0, corrected_issues=[])
        assert fb.corrected_issues == []


class TestNewResponseSchemas:
    def test_adaptation_pulse_schema(self):
        from app.schemas.feedback import AdaptationPulse
        pulse = AdaptationPulse(status="active", label="Adapted (8 feedbacks)", detail="Prioritizing Clarity")
        assert pulse.status == "active"

    def test_feedback_confirmation_schema(self):
        from app.schemas.feedback import FeedbackConfirmation
        conf = FeedbackConfirmation(
            summary="Feedback saved",
            effects=["term preservation guardrail activated"],
            stage_note="(2/3 feedbacks for full adaptation)",
        )
        assert len(conf.effects) == 1

    def test_adaptation_state_response(self):
        from app.schemas.feedback import AdaptationStateResponse
        asr = AdaptationStateResponse(feedback_count=5, retry_threshold=6.2)
        assert asr.adaptation_version == 0
        assert asr.damping_level == 0.15
