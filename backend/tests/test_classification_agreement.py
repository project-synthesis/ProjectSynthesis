"""Tests for ClassificationAgreement tracker."""

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
