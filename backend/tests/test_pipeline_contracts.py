"""Tests for PipelineResult validation edge cases."""

from types import MappingProxyType

import pytest
from pydantic import ValidationError

from app.schemas.pipeline_contracts import (
    DIMENSION_WEIGHTS,
    DimensionScores,
    OptimizationResult,
    PipelineResult,
)


def _base_kwargs() -> dict:
    """Minimum required fields for PipelineResult."""
    return dict(
        id="test-id",
        trace_id="trace-1",
        raw_prompt="test prompt",
        optimized_prompt="optimized prompt",
        task_type="coding",
        strategy_used="auto",
        changes_summary="improved clarity",
        provider="mock",
        model_used="test-model",
        scoring_mode="skipped",
        duration_ms=1000,
        status="completed",
        context_sources={},
    )


def test_pipeline_result_with_bool_context_sources():
    """Pure boolean flags are accepted (current 4-key schema)."""
    result = PipelineResult(
        **{**_base_kwargs(), "context_sources": {
            "codebase_context": True,
            "strategy_intelligence": False,
            "applied_patterns": True,
            "heuristic_analysis": True,
        }},
    )
    assert result.context_sources["codebase_context"] is True
    assert result.context_sources["strategy_intelligence"] is False


def test_pipeline_result_with_mixed_context_sources():
    """Reproduces the bug: enrichment_meta is a nested dict inside context_sources."""
    sources = {
        "codebase_context": True,
        "strategy_intelligence": True,
        "heuristic_analysis": True,
        "applied_patterns": False,
        "enrichment_meta": {
            "enrichment_profile": "code_aware",
            "repo_full_name": "owner/repo",
            "repo_branch": "main",
            "was_truncated": False,
        },
    }
    result = PipelineResult(**{**_base_kwargs(), "context_sources": sources})
    assert result.context_sources["enrichment_meta"]["repo_full_name"] == "owner/repo"
    assert result.context_sources["enrichment_meta"]["enrichment_profile"] == "code_aware"


def test_pipeline_result_with_none_context_sources():
    """None is coerced to empty dict by the field_validator."""
    result = PipelineResult(**{**_base_kwargs(), "context_sources": None})
    assert result.context_sources == {}


def test_pipeline_result_with_empty_context_sources():
    """Empty dict is accepted."""
    result = PipelineResult(**{**_base_kwargs(), "context_sources": {}})
    assert result.context_sources == {}


def test_pipeline_result_with_mapping_proxy():
    """MappingProxyType is coerced to plain dict."""
    sources = MappingProxyType({
        "codebase_context": True,
        "strategy_intelligence": False,
        "applied_patterns": True,
        "heuristic_analysis": True,
    })
    result = PipelineResult(**{**_base_kwargs(), "context_sources": sources})
    assert isinstance(result.context_sources, dict)
    assert result.context_sources["codebase_context"] is True


# ---------------------------------------------------------------------------
# F4 — OptimizationResult schema (audit-prompt hardening 2026-04-28)
# ---------------------------------------------------------------------------


class TestOptimizationResultSchema:
    """The optimizer LLM must not be able to declare a strategy.

    The orchestrator already knows which strategy was applied (it loaded the
    instructions itself).  Removing ``strategy_used`` from
    :class:`OptimizationResult` closes the divergence window where the LLM's
    freelance choice could overwrite the resolver's output (see
    ``docs/specs/audit-prompt-hardening-2026-04-28.md`` §F4).
    """

    def test_strategy_used_field_removed(self):
        """AC-F4-1: constructing with ``strategy_used`` raises ValidationError.

        ``OptimizationResult`` uses ``extra="forbid"`` so any unknown kwarg
        — including the removed ``strategy_used`` — must trigger Pydantic's
        forbidden-extras check.
        """
        with pytest.raises(ValidationError):
            OptimizationResult(
                optimized_prompt="x",
                changes_summary="y",
                strategy_used="chain-of-thought",
            )

    def test_optimization_result_minimal_construction(self):
        """AC-F4-2: minimal construction succeeds without ``strategy_used``."""
        result = OptimizationResult(
            optimized_prompt="x",
            changes_summary="y",
        )
        assert result.optimized_prompt == "x"
        assert result.changes_summary == "y"


# ---------------------------------------------------------------------------
# F3 — Per-task-type DIMENSION_WEIGHTS (audit-prompt hardening 2026-04-28)
# ---------------------------------------------------------------------------


class TestDimensionWeights:
    """Per-task-type weight schemas + ``DimensionScores.compute_overall``.

    Audit-class prompts (``task_type='analysis'``) have inherently
    different priorities than feature prompts: clarity/specificity/
    structure matter MORE; faithfulness (often hypothetical premises)
    and conciseness (audits are necessarily detailed) matter LESS.

    The fix introduces:
      * ``ANALYSIS_DIMENSION_WEIGHTS`` constant
      * ``get_dimension_weights(task_type)`` helper
      * ``DimensionScores.compute_overall(task_type=None)`` sibling method
        (preserves the existing ``@property def overall`` for the ~30
        backward-compat call sites)
      * ``SCORING_FORMULA_VERSION`` bump 3 → 4
      * Module-level invariant: both schemas sum to exactly 1.0

    See ``docs/specs/audit-prompt-hardening-2026-04-28.md`` §F3.
    """

    def test_default_uniform(self):
        """AC-F3-1: ``get_dimension_weights(None)`` returns the global ``DIMENSION_WEIGHTS``.

        Regression guard — all existing call sites that don't thread a
        task_type continue to use the v3 uniform schema.
        """
        from app.schemas.pipeline_contracts import get_dimension_weights

        assert get_dimension_weights(None) == DIMENSION_WEIGHTS

    def test_analysis_returns_analysis_schema(self):
        """AC-F3-2: ``get_dimension_weights('analysis')`` returns the analysis schema.

        Spec values: clarity 0.25, specificity 0.25, structure 0.20,
        faithfulness 0.20, conciseness 0.10.
        """
        from app.schemas.pipeline_contracts import (
            ANALYSIS_DIMENSION_WEIGHTS,
            get_dimension_weights,
        )

        result = get_dimension_weights("analysis")
        assert result == ANALYSIS_DIMENSION_WEIGHTS
        assert result["clarity"] == pytest.approx(0.25, abs=1e-9)
        assert result["specificity"] == pytest.approx(0.25, abs=1e-9)
        assert result["structure"] == pytest.approx(0.20, abs=1e-9)
        assert result["faithfulness"] == pytest.approx(0.20, abs=1e-9)
        assert result["conciseness"] == pytest.approx(0.10, abs=1e-9)

    def test_weights_sum_invariant(self):
        """AC-F3-3: both weight schemas sum to exactly 1.0.

        Mirrors the module-level ``assert`` invariant that fails fast at
        import time if a future edit breaks the sum-to-1 property.
        """
        from app.schemas.pipeline_contracts import ANALYSIS_DIMENSION_WEIGHTS

        assert sum(DIMENSION_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)
        assert sum(ANALYSIS_DIMENSION_WEIGHTS.values()) == pytest.approx(
            1.0, abs=1e-9,
        )

    def test_compute_overall_diverges(self):
        """AC-F3-4: analysis vs default schema yield meaningfully different overalls.

        Fixture: high clarity/specificity (9.0/9.0), low faithfulness
        (4.0).  Analysis schema upweights clarity/specificity → overall
        rises; faithfulness 0.20 (vs 0.26) → overall less penalized by
        the low value.  The two ``compute_overall`` calls must differ by
        ≥0.1 absolute.
        """
        scores = DimensionScores(
            clarity=9.0,
            specificity=9.0,
            structure=7.0,
            faithfulness=4.0,
            conciseness=6.0,
        )
        analysis_overall = scores.compute_overall(task_type="analysis")
        default_overall = scores.compute_overall(task_type=None)
        assert abs(analysis_overall - default_overall) >= 0.1, (
            f"Expected ≥0.1 absolute delta between analysis and default "
            f"overalls, got analysis={analysis_overall}, "
            f"default={default_overall}"
        )

    def test_compute_overall_default_matches_property(self):
        """AC-F3-5: ``compute_overall(None)`` equals the ``overall`` property.

        Backward-compat invariant — the new method falls through to the
        global ``DIMENSION_WEIGHTS`` when ``task_type`` is None,
        producing the same value the property has always produced.
        """
        scores = DimensionScores(
            clarity=7.5,
            specificity=8.2,
            structure=6.8,
            faithfulness=7.0,
            conciseness=8.5,
        )
        assert scores.compute_overall(task_type=None) == pytest.approx(
            scores.overall, abs=1e-9,
        )
