"""Tests for ClassificationAgreement tracker."""

from unittest.mock import patch

from app.services.classification_agreement import (
    ClassificationAgreement,
    _reset_agreement,
    get_classification_agreement,
)


def setup_function():
    """Reset singleton before each test."""
    _reset_agreement()


class TestClassificationAgreement:
    def test_both_agree(self):
        agr = ClassificationAgreement()
        agr.record("coding", "backend", "coding", "backend")
        assert agr.total == 1
        assert agr.task_type_agree == 1
        assert agr.domain_agree == 1
        assert agr.both_agree == 1

    def test_task_type_disagrees(self):
        agr = ClassificationAgreement()
        agr.record("creative", "general", "coding", "general")
        assert agr.total == 1
        assert agr.task_type_agree == 0
        assert agr.domain_agree == 1
        assert agr.both_agree == 0

    def test_domain_disagrees(self):
        agr = ClassificationAgreement()
        agr.record("coding", "general", "coding", "backend")
        assert agr.total == 1
        assert agr.task_type_agree == 1
        assert agr.domain_agree == 0
        assert agr.both_agree == 0

    def test_both_disagree(self):
        agr = ClassificationAgreement()
        agr.record("creative", "general", "coding", "database")
        assert agr.total == 1
        assert agr.task_type_agree == 0
        assert agr.domain_agree == 0
        assert agr.both_agree == 0

    def test_rates_zero_total(self):
        agr = ClassificationAgreement()
        rates = agr.rates()
        assert rates["total"] == 0
        assert rates["task_type_agreement_rate"] == 0.0
        assert rates["domain_agreement_rate"] == 0.0
        assert rates["both_agreement_rate"] == 0.0

    def test_rates_computed_correctly(self):
        agr = ClassificationAgreement()
        agr.record("coding", "backend", "coding", "backend")  # both agree
        agr.record("coding", "general", "coding", "backend")  # domain disagrees
        agr.record("creative", "backend", "coding", "backend")  # task disagrees
        agr.record("writing", "general", "writing", "general")  # both agree
        rates = agr.rates()
        assert rates["total"] == 4
        assert rates["task_type_agreement_rate"] == 0.75  # 3/4
        assert rates["domain_agreement_rate"] == 0.75  # 3/4
        assert rates["both_agreement_rate"] == 0.5  # 2/4

    def test_singleton_accessible(self):
        agr = get_classification_agreement()
        assert isinstance(agr, ClassificationAgreement)
        assert agr.total == 0

    def test_singleton_persists_across_calls(self):
        agr1 = get_classification_agreement()
        agr1.record("coding", "backend", "coding", "backend")
        agr2 = get_classification_agreement()
        assert agr2.total == 1  # same instance

    def test_prompt_snippet_in_record(self):
        """prompt_snippet parameter accepted without error."""
        agr = ClassificationAgreement()
        agr.record("creative", "general", "coding", "backend", prompt_snippet="Design a system for...")
        assert agr.total == 1

    def test_strategy_intel_hit_tracking(self):
        agr = ClassificationAgreement()
        agr.record_strategy_intel(had_intel=True)
        agr.record_strategy_intel(had_intel=False)
        agr.record_strategy_intel(had_intel=True)
        assert agr.strategy_intel_total == 3
        assert agr.strategy_intel_hits == 2

    def test_strategy_intelligence_hit_rate_in_rates(self):
        agr = ClassificationAgreement()
        agr.record_strategy_intel(had_intel=True)
        agr.record_strategy_intel(had_intel=True)
        agr.record_strategy_intel(had_intel=False)
        rates = agr.rates()
        assert rates["strategy_intelligence_hit_rate"] == 0.67

    def test_strategy_intelligence_hit_rate_zero(self):
        agr = ClassificationAgreement()
        rates = agr.rates()
        assert rates["strategy_intelligence_hit_rate"] == 0.0


class TestCrossProcessForwarding:
    """E1b: Cross-process classification agreement bridge tests."""

    def test_cross_process_default_false(self):
        agr = ClassificationAgreement()
        assert agr._cross_process is False

    def test_cross_process_record_fires_forward(self):
        agr = ClassificationAgreement(_cross_process=True)
        with patch.object(agr, "_forward") as mock_fwd:
            agr.record("coding", "backend", "writing", "general")
            mock_fwd.assert_called_once()
            call_args = mock_fwd.call_args
            assert call_args[0][0] == "classification_agreement_record"
            assert call_args[0][1]["heuristic_task_type"] == "coding"
            assert call_args[0][1]["llm_task_type"] == "writing"

    def test_cross_process_strategy_intel_fires_forward(self):
        agr = ClassificationAgreement(_cross_process=True)
        with patch.object(agr, "_forward") as mock_fwd:
            agr.record_strategy_intel(had_intel=True)
            mock_fwd.assert_called_once()
            assert mock_fwd.call_args[0][0] == "classification_agreement_strategy_intel"
            assert mock_fwd.call_args[0][1]["had_intel"] is True

    def test_forward_failure_preserves_local_state(self):
        """Forward failure must not prevent local counter increment."""
        agr = ClassificationAgreement(_cross_process=True)
        # Patch _forward entirely to simulate total failure — record() calls
        # _forward after incrementing counters, so local state is always set.
        with patch.object(agr, "_forward", side_effect=Exception("network down")):
            # _forward is called inside record() but record() doesn't wrap
            # it in try/except — however, _forward itself catches exceptions.
            # We sidestep by patching _forward to raise, which bubbles up.
            # The test verifies counters were incremented BEFORE the forward call.
            try:
                agr.record("coding", "backend", "writing", "general")
            except Exception:
                pass
        assert agr.total == 1  # local state preserved before forward

    def test_backend_record_does_not_reforward(self):
        """Backend singleton has _cross_process=False -> no forwarding."""
        agr = ClassificationAgreement()  # default: _cross_process=False
        with patch.object(agr, "_forward") as mock_fwd:
            agr.record("coding", "backend", "coding", "backend")
            mock_fwd.assert_not_called()
