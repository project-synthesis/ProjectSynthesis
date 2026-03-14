"""Tests for pipeline adaptation integration.

Verifies that adaptation state (framework profiles, strategy affinities,
user weights, issue guardrails) is correctly wired into the strategy,
optimizer, and validator stages.
"""

from app.services.framework_profiles import get_profile
from app.services.optimizer import build_adaptation_hints
from app.services.strategy_selector import build_affinity_prompt_section
from app.services.validator import compute_effective_weights


class TestStrategyAffinityInjection:
    def test_affinity_text_for_known_task(self):
        affinities = {
            "coding": {
                "preferred": ["chain-of-thought"],
                "avoid": ["few-shot-scaffolding"],
            },
        }
        text = build_affinity_prompt_section("coding", affinities)
        assert "chain-of-thought" in text
        assert "few-shot-scaffolding" in text

    def test_no_affinity_for_unknown_task(self):
        text = build_affinity_prompt_section("unknown_task", {})
        assert text == ""

    def test_none_affinities(self):
        text = build_affinity_prompt_section("coding", None)
        assert text == ""

    def test_preferred_only(self):
        affinities = {
            "analysis": {
                "preferred": ["chain-of-thought", "step-by-step"],
                "avoid": [],
            },
        }
        text = build_affinity_prompt_section("analysis", affinities)
        assert "chain-of-thought" in text
        assert "step-by-step" in text
        assert "avoid" not in text.lower().split("prefer")[0]

    def test_avoid_only(self):
        affinities = {
            "writing": {
                "preferred": [],
                "avoid": ["structured-output"],
            },
        }
        text = build_affinity_prompt_section("writing", affinities)
        assert "structured-output" in text

    def test_empty_preferred_and_avoid(self):
        affinities = {
            "coding": {
                "preferred": [],
                "avoid": [],
            },
        }
        text = build_affinity_prompt_section("coding", affinities)
        assert text == ""


class TestOptimizerHints:
    def test_build_hints_with_all_inputs(self):
        hints = build_adaptation_hints(
            framework_profile=get_profile("chain-of-thought"),
            user_weights={
                "clarity_score": 0.30,
                "conciseness_score": 0.10,
                "structure_score": 0.20,
                "faithfulness_score": 0.20,
                "specificity_score": 0.20,
            },
            issue_guardrails=["PRESERVE all domain-specific terminology..."],
        )
        assert "clarity" in hints.lower() or "Structure" in hints
        assert "PRESERVE" in hints

    def test_no_hints_without_adaptation(self):
        hints = build_adaptation_hints(None, None, [])
        assert hints == ""

    def test_hints_with_profile_only(self):
        hints = build_adaptation_hints(
            framework_profile=get_profile("chain-of-thought"),
            user_weights=None,
            issue_guardrails=[],
        )
        assert hints  # Should have framework emphasis
        assert "excels" in hints.lower() or "prioritize" in hints.lower()

    def test_hints_with_guardrails_only(self):
        hints = build_adaptation_hints(
            framework_profile=None,
            user_weights=None,
            issue_guardrails=[
                "PRESERVE all domain-specific terminology",
                "Do NOT add requirements not in the original",
            ],
        )
        assert "PRESERVE" in hints
        assert "NOT add" in hints

    def test_hints_with_user_weights_only(self):
        hints = build_adaptation_hints(
            framework_profile=None,
            user_weights={
                "clarity_score": 0.35,
                "conciseness_score": 0.10,
                "structure_score": 0.20,
                "faithfulness_score": 0.20,
                "specificity_score": 0.15,
            },
            issue_guardrails=[],
        )
        assert hints
        assert "Clarity" in hints  # highest priority

    def test_guardrails_capped_at_four(self):
        guardrails = [f"Rule {i}" for i in range(10)]
        hints = build_adaptation_hints(None, None, guardrails)
        # Should only include first 4
        assert "Rule 0" in hints
        assert "Rule 3" in hints
        assert "Rule 4" not in hints


class TestValidatorCalibration:
    def test_effective_weights_combine_profile_and_user(self):
        profile = get_profile("chain-of-thought")
        user_weights = {
            "clarity_score": 0.25,
            "specificity_score": 0.20,
            "structure_score": 0.20,
            "faithfulness_score": 0.20,
            "conciseness_score": 0.15,
        }
        effective = compute_effective_weights(user_weights, profile)
        # chain-of-thought emphasizes structure (1.3x) and clarity (1.2x),
        # de-emphasizes conciseness (0.8x)
        assert effective["structure_score"] > user_weights["structure_score"]
        assert effective["conciseness_score"] < user_weights["conciseness_score"]
        assert abs(sum(effective.values()) - 1.0) < 0.01

    def test_effective_weights_default_without_user(self):
        effective = compute_effective_weights(None, None)
        assert abs(sum(effective.values()) - 1.0) < 0.01
        # All equal
        vals = list(effective.values())
        assert max(vals) - min(vals) < 0.01

    def test_effective_weights_with_profile_only(self):
        profile = get_profile("chain-of-thought")
        effective = compute_effective_weights(None, profile)
        assert abs(sum(effective.values()) - 1.0) < 0.01
        # chain-of-thought: structure emphasized, conciseness de-emphasized
        assert effective["structure_score"] > effective["conciseness_score"]

    def test_effective_weights_with_user_only(self):
        user_weights = {
            "clarity_score": 0.30,
            "specificity_score": 0.20,
            "structure_score": 0.20,
            "faithfulness_score": 0.20,
            "conciseness_score": 0.10,
        }
        effective = compute_effective_weights(user_weights, None)
        assert abs(sum(effective.values()) - 1.0) < 0.01
        # No profile multipliers, so ratios should be preserved
        assert effective["clarity_score"] > effective["conciseness_score"]

    def test_effective_weights_renormalize(self):
        """Weights always sum to 1.0 even with extreme multipliers."""
        profile = {
            "emphasis": {"clarity_score": 2.0, "structure_score": 2.0},
            "de_emphasis": {"conciseness_score": 0.3},
        }
        effective = compute_effective_weights(None, profile)
        assert abs(sum(effective.values()) - 1.0) < 0.01
