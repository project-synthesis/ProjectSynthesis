from app.services.issue_guardrails import (
    build_issue_guardrails,
    build_issue_verification_prompt,
)


class TestBuildIssueGuardrails:
    def test_no_guardrails_below_threshold(self):
        assert build_issue_guardrails({"lost_key_terms": 1}, None) == ""

    def test_guardrails_at_threshold(self):
        result = build_issue_guardrails({"lost_key_terms": 2}, None)
        assert "PRESERVE" in result

    def test_max_four_guardrails(self):
        freq = {
            k: 5
            for k in [
                "lost_key_terms",
                "changed_meaning",
                "hallucinated_content",
                "too_verbose",
                "too_vague",
                "broken_structure",
            ]
        }
        result = build_issue_guardrails(freq, None)
        assert result.count("- ") <= 4

    def test_merges_user_and_framework_freq(self):
        result = build_issue_guardrails(
            {"lost_key_terms": 1}, {"lost_key_terms": 1}
        )
        assert "PRESERVE" in result


class TestBuildIssueVerificationPrompt:
    def test_no_verification_below_threshold(self):
        assert build_issue_verification_prompt({"lost_key_terms": 1}) is None

    def test_term_check_for_lost_key_terms(self):
        result = build_issue_verification_prompt({"lost_key_terms": 2})
        assert result is not None
        assert "TERM CHECK" in result

    def test_intent_check_for_changed_meaning(self):
        result = build_issue_verification_prompt({"changed_meaning": 3})
        assert "INTENT CHECK" in result

    def test_addition_check_for_hallucinated(self):
        result = build_issue_verification_prompt({"hallucinated_content": 2})
        assert "ADDITION CHECK" in result
