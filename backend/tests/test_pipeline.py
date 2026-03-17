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
