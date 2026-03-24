"""Tests for the pipeline orchestrator."""

from unittest.mock import AsyncMock, patch

import pytest

from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
)
from app.services.pipeline import PipelineOrchestrator
from app.services.pipeline_constants import (
    apply_domain_gate,
    resolve_effective_strategy,
    resolve_fallback_strategy,
    semantic_check,
)


def _make_analysis(**overrides):
    defaults = dict(
        task_type="coding", weaknesses=["vague"], strengths=["concise"],
        selected_strategy="chain-of-thought", strategy_rationale="good for coding",
        confidence=0.9,
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _make_optimization(**overrides):
    defaults = dict(
        optimized_prompt="Write a Python function that sorts a list in ascending order.",
        changes_summary="Added specificity: language, operation, order.",
        strategy_used="chain-of-thought",
    )
    defaults.update(overrides)
    return OptimizationResult(**defaults)


def _make_scores(a_clarity=4.0, b_clarity=8.0):
    return ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=a_clarity, specificity=3.0, structure=5.0, faithfulness=5.0, conciseness=6.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=b_clarity, specificity=8.0, structure=7.0, faithfulness=9.0, conciseness=7.0,
        ),
    )


@pytest.fixture
def mock_provider():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    # Streaming delegates to non-streaming (mirrors base class default),
    # so tests that set side_effect on complete_parsed work for both paths.
    async def _streaming_delegate(**kw):
        return await provider.complete_parsed(**kw)

    provider.complete_parsed_streaming.side_effect = _streaming_delegate
    return provider


@pytest.fixture
def orchestrator(tmp_path):
    """Create orchestrator with minimal prompts dir."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    strategies = prompts / "strategies"
    strategies.mkdir()
    (prompts / "agent-guidance.md").write_text("System prompt.")
    (prompts / "analyze.md").write_text("{{raw_prompt}}\n{{available_strategies}}")
    (prompts / "optimize.md").write_text(
        "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}\n"
        "<codebase-context>\n{{codebase_guidance}}\n{{codebase_context}}\n</codebase-context>\n"
        "<adaptation>\n{{adaptation_state}}\n</adaptation>"
    )
    (prompts / "scoring.md").write_text("Score these prompts.")
    (prompts / "manifest.json").write_text(
        '{"analyze.md": {"required": ["raw_prompt", "available_strategies"], "optional": []},'
        '"optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"], '
        '"optional": ["codebase_guidance", "codebase_context", "adaptation_state"]},'
        '"scoring.md": {"required": [], "optional": []}}'
    )
    (strategies / "chain-of-thought.md").write_text("Think step by step.")
    (strategies / "auto.md").write_text("Auto-select.")
    return PipelineOrchestrator(prompts_dir=prompts)


class TestPipelineOrchestrator:
    async def test_full_flow_emits_correct_events(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        events = []
        async for event in orchestrator.run(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider, db=db_session,
        ):
            events.append(event)
        event_names = [e.event for e in events]
        assert "optimization_start" in event_names
        assert "optimization_complete" in event_names
        start_idx = event_names.index("optimization_start")
        complete_idx = event_names.index("optimization_complete")
        assert start_idx < complete_idx
        assert mock_provider.complete_parsed.call_count == 3

    async def test_scorer_gets_neutral_ab_labels(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        scorer_call = mock_provider.complete_parsed.call_args_list[2]
        user_msg = scorer_call.kwargs.get("user_message", "")
        assert "<prompt-a>" in user_msg
        assert "<prompt-b>" in user_msg
        assert "original" not in user_msg.lower()
        assert "optimized" not in user_msg.lower()

    async def test_low_confidence_overrides_to_auto(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(confidence=0.4, selected_strategy="few-shot"),
            _make_optimization(strategy_used="auto"),
            _make_scores(),
        ]
        events = []
        async for event in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            events.append(event)
        optimizer_call = mock_provider.complete_parsed.call_args_list[1]
        user_msg = optimizer_call.kwargs.get("user_message", "")
        assert "Auto-select" in user_msg

    async def test_error_event_on_provider_failure(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = RuntimeError("LLM unavailable")
        events = []
        async for event in orchestrator.run(
            raw_prompt="test", provider=mock_provider, db=db_session,
        ):
            events.append(event)
        event_names = [e.event for e in events]
        assert "error" in event_names

    async def test_score_deltas_computed_correctly(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(),
            _make_scores(a_clarity=4.0, b_clarity=8.0),
        ]
        with patch("app.services.pipeline.random.choice", return_value=True):  # original_first
            events = []
            async for event in orchestrator.run(
                raw_prompt="test", provider=mock_provider, db=db_session,
            ):
                events.append(event)
        score_card = next(e for e in events if e.event == "score_card")
        # Hybrid scoring blends LLM + heuristic, so exact values change.
        # Verify deltas are present and directionally correct (optimized > original).
        assert "clarity" in score_card.data["deltas"]
        assert score_card.data["deltas"]["clarity"] > 0

    async def test_strategy_override(self, orchestrator, mock_provider, db_session):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(selected_strategy="chain-of-thought"),
            _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test", provider=mock_provider, db=db_session,
            strategy_override="chain-of-thought",
        ):
            pass
        assert mock_provider.complete_parsed.call_count == 3

    async def test_low_confidence_overrides_domain_to_general(self, orchestrator, mock_provider, db_session):
        """Domain confidence gate: confidence < 0.6 forces domain='general'."""
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(confidence=0.5, domain="backend"),
            _make_optimization(),
            _make_scores(),
        ]
        events = []
        async for event in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            events.append(event)
        complete = next(e for e in events if e.event == "optimization_complete")
        assert complete.data["domain"] == "general"

    async def test_high_confidence_preserves_domain(self, orchestrator, mock_provider, db_session):
        """Domain preserved when confidence >= 0.6."""
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(confidence=0.8, domain="backend"),
            _make_optimization(),
            _make_scores(),
        ]
        events = []
        async for event in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            events.append(event)
        complete = next(e for e in events if e.event == "optimization_complete")
        assert complete.data["domain"] == "backend"

    async def test_scoring_disabled_skips_phase_3(self, orchestrator, mock_provider, db_session, tmp_path):
        """When enable_scoring=False, pipeline skips Phase 3 and returns null scores."""
        import json

        # Write preferences with scoring disabled
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text(json.dumps({
            "schema_version": 1,
            "models": {"analyzer": "sonnet", "optimizer": "opus", "scorer": "sonnet"},
            "pipeline": {"enable_explore": True, "enable_scoring": False, "enable_adaptation": True},
            "defaults": {"strategy": "auto"},
        }))

        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(),
            # No third call — scoring is skipped
        ]

        with patch("app.services.pipeline.DATA_DIR", tmp_path):
            events = []
            async for event in orchestrator.run(
                raw_prompt="test prompt for scoring disabled",
                provider=mock_provider, db=db_session,
            ):
                events.append(event)

        event_types = [e.event for e in events]
        # score_card should NOT be emitted
        assert "score_card" not in event_types
        # optimization_complete should still be emitted
        assert "optimization_complete" in event_types
        # Provider should only be called twice (analyze + optimize, no score)
        assert mock_provider.complete_parsed.call_count == 2
        # Result should have scoring_mode="skipped"
        complete_event = next(e for e in events if e.event == "optimization_complete")
        assert complete_event.data.get("scoring_mode") == "skipped"
        assert complete_event.data.get("optimized_scores") is None

    async def test_unknown_strategy_falls_back_to_auto(self, orchestrator, mock_provider, db_session):
        """When analyzer selects a strategy that doesn't exist on disk, fall back to 'auto'."""
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(selected_strategy="tree-of-thought", confidence=0.9),
            _make_optimization(strategy_used="auto"),
            _make_scores(),
        ]
        events = []
        async for event in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            events.append(event)
        # Optimizer should receive auto.md content (not tree-of-thought)
        optimizer_call = mock_provider.complete_parsed.call_args_list[1]
        user_msg = optimizer_call.kwargs.get("user_message", "")
        assert "Auto-select" in user_msg


class TestResolveFallbackStrategy:
    def test_auto_exists(self):
        """When 'auto' is in the list, returns 'auto'."""
        assert resolve_fallback_strategy(["auto", "chain-of-thought"]) == "auto"

    def test_auto_missing(self):
        """When 'auto' is not in the list, returns the first available strategy."""
        assert resolve_fallback_strategy(["chain-of-thought", "few-shot"]) == "chain-of-thought"

    def test_empty_list(self):
        """When no strategies exist, returns 'auto' anyway (graceful degradation)."""
        assert resolve_fallback_strategy([]) == "auto"


class TestSemanticCheck:
    def test_reduces_confidence_for_coding_without_keywords(self):
        result = semantic_check("coding", "Help me organize my day", 0.8)
        assert result == pytest.approx(0.6)

    def test_no_reduction_for_coding_with_keywords(self):
        result = semantic_check("coding", "Write a function to parse JSON", 0.8)
        assert result == 0.8

    def test_no_reduction_for_non_coding(self):
        result = semantic_check("writing", "Help me organize my day", 0.8)
        assert result == 0.8

    def test_floor_at_zero(self):
        result = semantic_check("coding", "Help me organize my day", 0.1)
        assert result == 0.0


class TestApplyDomainGate:
    def test_preserves_domain_when_confident(self):
        assert apply_domain_gate("backend", 0.8) == "backend"

    def test_overrides_to_general_when_low_confidence(self):
        assert apply_domain_gate("backend", 0.5) == "general"

    def test_null_domain_defaults_to_general(self):
        assert apply_domain_gate(None, 0.9) == "general"

    def test_boundary_at_0_6(self):
        assert apply_domain_gate("frontend", 0.6) == "frontend"
        assert apply_domain_gate("frontend", 0.59) == "general"


class TestResolveEffectiveStrategy:
    def test_valid_strategy_passes_through(self):
        result = resolve_effective_strategy(
            "chain-of-thought", ["auto", "chain-of-thought"], set(), 0.9, None, "t1",
        )
        assert result == "chain-of-thought"

    def test_unknown_strategy_falls_back(self):
        result = resolve_effective_strategy(
            "hallucinated-strategy", ["auto", "chain-of-thought"], set(), 0.9, None, "t1",
        )
        assert result == "auto"

    def test_blocked_strategy_falls_back(self):
        result = resolve_effective_strategy(
            "chain-of-thought", ["auto", "chain-of-thought"], {"chain-of-thought"}, 0.9, None, "t1",
        )
        assert result == "auto"

    def test_low_confidence_triggers_gate(self):
        result = resolve_effective_strategy(
            "chain-of-thought", ["auto", "chain-of-thought"], set(), 0.5, None, "t1",
        )
        assert result == "auto"

    def test_override_bypasses_all_gates(self):
        result = resolve_effective_strategy(
            "chain-of-thought", ["auto", "chain-of-thought"],
            {"chain-of-thought"},  # blocked
            0.3,  # low confidence
            "few-shot",  # explicit override
            "t1",
        )
        assert result == "few-shot"

    def test_blocked_skipped_when_override_set(self):
        result = resolve_effective_strategy(
            "chain-of-thought", ["auto", "chain-of-thought"],
            {"chain-of-thought"},  # blocked
            0.9,
            "chain-of-thought",  # override matches blocked
            "t1",
        )
        assert result == "chain-of-thought"


class TestStrategyFiltering:
    def test_format_available_filters_blocked(self, orchestrator):
        """format_available excludes blocked strategies from the bullet list."""
        result = orchestrator.strategy_loader.format_available(
            blocked={"chain-of-thought"},
        )
        assert "chain-of-thought" not in result
        assert "auto" in result

    def test_format_available_no_blocked(self, orchestrator):
        """format_available includes all strategies when no blocked set."""
        result = orchestrator.strategy_loader.format_available()
        assert "chain-of-thought" in result
        assert "auto" in result


class TestPipelinePerformanceParams:
    """Tests for effort, max_tokens, and cache_ttl parameters per phase."""

    async def test_analyze_phase_uses_effort_preference(
        self, orchestrator, mock_provider, db_session
    ):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        # First call is analyze
        analyze_call = mock_provider.complete_parsed.call_args_list[0]
        assert analyze_call.kwargs["effort"] == "low"

    async def test_analyze_phase_uses_reduced_max_tokens(
        self, orchestrator, mock_provider, db_session
    ):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        analyze_call = mock_provider.complete_parsed.call_args_list[0]
        assert analyze_call.kwargs["max_tokens"] == 4096

    async def test_score_phase_uses_effort_preference(
        self, orchestrator, mock_provider, db_session
    ):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        # Third call is score
        score_call = mock_provider.complete_parsed.call_args_list[2]
        assert score_call.kwargs["effort"] == "low"

    async def test_score_phase_uses_reduced_max_tokens(
        self, orchestrator, mock_provider, db_session
    ):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        score_call = mock_provider.complete_parsed.call_args_list[2]
        assert score_call.kwargs["max_tokens"] == 4096

    async def test_score_phase_passes_cache_ttl(
        self, orchestrator, mock_provider, db_session
    ):
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        score_call = mock_provider.complete_parsed.call_args_list[2]
        assert score_call.kwargs["cache_ttl"] == "1h"

    async def test_optimize_phase_unchanged(
        self, orchestrator, mock_provider, db_session
    ):
        """Optimize phase keeps existing effort='high', streaming=True, dynamic max_tokens."""
        mock_provider.complete_parsed.side_effect = [
            _make_analysis(), _make_optimization(), _make_scores(),
        ]
        async for _ in orchestrator.run(
            raw_prompt="test prompt", provider=mock_provider, db=db_session,
        ):
            pass
        # Second call is optimize — goes through complete_parsed_streaming
        optimize_call = mock_provider.complete_parsed_streaming.call_args_list[0]
        assert optimize_call.kwargs["effort"] == "high"
        # Dynamic max_tokens should be >= 16384
        assert optimize_call.kwargs["max_tokens"] >= 16384
