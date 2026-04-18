"""Tests for the batch seeding pipeline (`batch_pipeline.py`).

Covers the three divergences from the regular optimization pipeline
that the seed-alignment plan fixes:
    1. Resolved routing tier must be threaded end-to-end (not hardcoded).
    2. ContextEnrichmentService.enrich() is the single enrichment entry.
       The enrichment's divergence_alerts must reach the optimize render.
    3. Each persisted row emits `optimization_created` on the event bus,
       matching the regular pipeline's downstream contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.batch_pipeline import PendingOptimization, run_single_prompt
from app.services.prompt_loader import PromptLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _analysis(**overrides: Any) -> AnalysisResult:
    defaults = dict(
        task_type="coding",
        domain="backend",
        intent_label="generic optimization",
        weaknesses=["vague"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="coding tasks benefit from decomposition",
        confidence=0.9,
    )
    defaults.update(overrides)
    return AnalysisResult(**defaults)


def _optimization(**overrides: Any) -> OptimizationResult:
    defaults = dict(
        optimized_prompt=(
            "Write a Python function that sorts a list of integers in "
            "ascending order and returns the sorted list."
        ),
        changes_summary="Added specificity: language, input type, return value.",
        strategy_used="chain-of-thought",
    )
    defaults.update(overrides)
    return OptimizationResult(**defaults)


def _scores() -> ScoreResult:
    return ScoreResult(
        prompt_a_scores=DimensionScores(
            clarity=4.0, specificity=3.0, structure=5.0,
            faithfulness=5.0, conciseness=6.0,
        ),
        prompt_b_scores=DimensionScores(
            clarity=8.0, specificity=8.0, structure=7.0,
            faithfulness=9.0, conciseness=7.0,
        ),
    )


def _suggestions() -> SuggestionsOutput:
    return SuggestionsOutput(
        suggestions=[{"text": "Add an example input/output.", "source": "heuristic"}],
    )


def _build_prompts_dir(root: Path) -> Path:
    """Create a minimal but sufficient prompts directory."""
    prompts = root / "prompts"
    prompts.mkdir()
    strategies = prompts / "strategies"
    strategies.mkdir()
    (prompts / "agent-guidance.md").write_text("System prompt.")
    (prompts / "analyze.md").write_text(
        "Prompt: {{raw_prompt}}\nStrategies: {{available_strategies}}\n"
        "Domains: {{known_domains}}"
    )
    (prompts / "optimize.md").write_text(
        "Raw: {{raw_prompt}}\nAnalysis: {{analysis_summary}}\n"
        "Strategy: {{strategy_instructions}}\n"
        "Codebase: {{codebase_context}}\n"
        "StrategyIntel: {{strategy_intelligence}}\n"
        "Patterns: {{applied_patterns}}\n"
        "FewShot: {{few_shot_examples}}\n"
        "Divergence: {{divergence_alerts}}"
    )
    (prompts / "scoring.md").write_text("Score A/B.")
    (prompts / "suggest.md").write_text(
        "Optimized: {{optimized_prompt}}\nScores: {{scores}}\n"
        "Weaknesses: {{weaknesses}}\nStrategy: {{strategy_used}}\n"
        "Deltas: {{score_deltas}}\nTrajectory: {{score_trajectory}}"
    )
    (prompts / "manifest.json").write_text(
        '{"analyze.md": {"required": ["raw_prompt", "available_strategies", "known_domains"], "optional": []},'
        '"optimize.md": {"required": ["raw_prompt", "analysis_summary", "strategy_instructions"], '
        '"optional": ["codebase_context", "strategy_intelligence", "applied_patterns", "few_shot_examples", "divergence_alerts"]},'
        '"scoring.md": {"required": [], "optional": []},'
        '"suggest.md": {"required": ["optimized_prompt", "scores", "weaknesses", "strategy_used", "score_deltas", "score_trajectory"], "optional": []}}'
    )
    (strategies / "chain-of-thought.md").write_text(
        "---\nname: chain-of-thought\n---\n\nThink step by step."
    )
    (strategies / "auto.md").write_text(
        "---\nname: auto\n---\n\nAuto-select."
    )
    return prompts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider() -> AsyncMock:
    """LLMProvider mock whose streaming delegates to complete_parsed.

    Tests only configure `complete_parsed.side_effect` — optimize's streaming
    call then resolves through the delegation. Matches conftest.mock_provider.
    """
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"

    async def _streaming_delegate(**kw: Any) -> Any:
        return await provider.complete_parsed(**kw)

    provider.complete_parsed_streaming.side_effect = _streaming_delegate
    return provider


@pytest.fixture
def mock_embedding_service() -> AsyncMock:
    svc = AsyncMock()
    svc.aembed_single = AsyncMock(return_value=np.zeros(384, dtype="float32"))
    return svc


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    return _build_prompts_dir(tmp_path)


@pytest.fixture
def prompt_loader(prompts_dir: Path) -> PromptLoader:
    return PromptLoader(prompts_dir)


@pytest.fixture(autouse=True)
def isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point DATA_DIR at an empty tmp_path so PreferencesService starts clean.

    Without this, batch_pipeline reads the developer's real preferences and
    tests become nondeterministic.
    """
    import app.config as _cfg

    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path)


# ---------------------------------------------------------------------------
# Tests — Fix 1: tier propagation
# ---------------------------------------------------------------------------


class TestTierPropagation:
    """run_single_prompt must thread the resolved tier into routing_tier."""

    async def test_passthrough_tier_is_persisted(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
    ) -> None:
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        result: PendingOptimization = await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="passthrough",
        )

        assert result.status == "completed", f"run failed: {result.error}"
        assert result.routing_tier == "passthrough"

    async def test_sampling_tier_is_persisted(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
    ) -> None:
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        result: PendingOptimization = await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="sampling",
        )

        assert result.status == "completed", f"run failed: {result.error}"
        assert result.routing_tier == "sampling"

    async def test_internal_is_default_tier(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
    ) -> None:
        """Default tier keeps back-compat: callers that never pass `tier`
        still persist as 'internal', the historical behavior."""
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        result: PendingOptimization = await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
        )

        assert result.status == "completed", f"run failed: {result.error}"
        assert result.routing_tier == "internal"
