"""Tests for score-informed strategy recommendation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.pipeline_constants import (
    StrategyRecommendation,
    recommend_strategy_from_history,
    resolve_effective_strategy,
)


def _rand_emb(seed: int = 42, dim: int = 768) -> np.ndarray:
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


class TestRecommendStrategyFromHistory:
    """recommend_strategy_from_history: score-informed strategy selection."""

    @pytest.mark.asyncio
    async def test_cold_start_returns_none(self, db_session):
        """Empty DB returns no recommendation."""
        result = await recommend_strategy_from_history(
            raw_prompt="Write a REST API",
            db=db_session,
            available_strategies=["auto", "chain-of-thought"],
            trace_id="test",
            prompt_embedding=_rand_emb(1),
        )
        assert result.recommended_strategy is None
        assert result.evidence_count == 0

    @pytest.mark.asyncio
    async def test_insufficient_samples_returns_none(self, db_session):
        """Below min_samples per strategy returns no recommendation."""
        from app.models import Optimization

        # Only 2 optimizations per strategy (below STRATEGY_REC_MIN_SAMPLES=3)
        for i in range(2):
            opt = Optimization(
                raw_prompt=f"Test prompt {i}",
                status="completed",
                strategy_used="auto",
                overall_score=8.0,
                embedding=_rand_emb(i).tobytes(),
            )
            db_session.add(opt)
        await db_session.commit()

        result = await recommend_strategy_from_history(
            raw_prompt="Test prompt",
            db=db_session,
            available_strategies=["auto"],
            trace_id="test",
            prompt_embedding=_rand_emb(0),
        )
        assert result.recommended_strategy is None

    @pytest.mark.asyncio
    async def test_recommends_highest_scoring_strategy(self, db_session):
        """Strategy with highest score-weighted average wins."""
        from app.models import Optimization

        base_emb = _rand_emb(42)

        # Strategy A: high scores (8-9)
        for i in range(4):
            db_session.add(Optimization(
                raw_prompt=f"Coding prompt {i}",
                status="completed",
                strategy_used="strategy-a",
                overall_score=8.5,
                embedding=base_emb.tobytes(),
            ))

        # Strategy B: low scores (4-5)
        for i in range(4):
            db_session.add(Optimization(
                raw_prompt=f"Coding prompt B{i}",
                status="completed",
                strategy_used="strategy-b",
                overall_score=4.5,
                embedding=base_emb.tobytes(),
            ))
        await db_session.commit()

        result = await recommend_strategy_from_history(
            raw_prompt="Coding prompt",
            db=db_session,
            available_strategies=["strategy-a", "strategy-b"],
            trace_id="test",
            prompt_embedding=base_emb,
        )
        assert result.recommended_strategy == "strategy-a"

    @pytest.mark.asyncio
    async def test_filters_unavailable_strategies(self, db_session):
        """Strategies not in available list are excluded."""
        from app.models import Optimization

        base_emb = _rand_emb(42)
        for i in range(5):
            db_session.add(Optimization(
                raw_prompt=f"Prompt {i}",
                status="completed",
                strategy_used="deleted-strategy",
                overall_score=9.0,
                embedding=base_emb.tobytes(),
            ))
        await db_session.commit()

        result = await recommend_strategy_from_history(
            raw_prompt="Prompt",
            db=db_session,
            available_strategies=["auto", "chain-of-thought"],
            trace_id="test",
            prompt_embedding=base_emb,
        )
        assert result.recommended_strategy is None

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_empty(self):
        """Embedding service failure returns empty result gracefully."""
        mock_db_session = MagicMock()
        # When prompt_embedding is not provided and embedding fails,
        # the function should catch the error and return empty
        with patch("app.services.embedding_service.EmbeddingService") as mock_cls:
            mock_svc = MagicMock()
            mock_svc.aembed_single = AsyncMock(side_effect=RuntimeError("model not loaded"))
            mock_cls.return_value = mock_svc

            result = await recommend_strategy_from_history(
                raw_prompt="Test",
                db=mock_db_session,
                available_strategies=["auto"],
                trace_id="test",
            )
        assert result.recommended_strategy is None


class TestResolveEffectiveStrategyDataRecommendation:
    """resolve_effective_strategy with data_recommendation parameter."""

    def test_backward_compatible_without_recommendation(self):
        """Calling without data_recommendation preserves existing behavior."""
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought"],
            blocked_strategies=set(),
            confidence=0.9,
            strategy_override=None,
            trace_id="test",
        )
        assert result == "auto"

    def test_uses_recommendation_below_confidence_gate(self):
        """Data recommendation used when confidence is below gate."""
        rec = StrategyRecommendation(
            recommended_strategy="chain-of-thought",
            confidence_boost=0.15,
            evidence_count=10,
            score_by_strategy={"chain-of-thought": 8.5},
        )
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought"],
            blocked_strategies=set(),
            confidence=0.5,
            strategy_override=None,
            trace_id="test",
            data_recommendation=rec,
        )
        assert result == "chain-of-thought"

    def test_ignores_recommendation_above_confidence_gate(self):
        """Data recommendation ignored when analyzer confidence is high."""
        rec = StrategyRecommendation(
            recommended_strategy="chain-of-thought",
            confidence_boost=0.15,
            evidence_count=10,
            score_by_strategy={"chain-of-thought": 8.5},
        )
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought"],
            blocked_strategies=set(),
            confidence=0.9,
            strategy_override=None,
            trace_id="test",
            data_recommendation=rec,
        )
        assert result == "auto"

    def test_explicit_override_wins_over_recommendation(self):
        """Explicit strategy override always takes precedence."""
        rec = StrategyRecommendation(
            recommended_strategy="chain-of-thought",
            confidence_boost=0.15,
            evidence_count=10,
        )
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought", "structured-output"],
            blocked_strategies=set(),
            confidence=0.5,
            strategy_override="structured-output",
            trace_id="test",
            data_recommendation=rec,
        )
        assert result == "structured-output"

    def test_blocked_recommendation_not_used(self):
        """Blocked recommended strategy falls through to confidence gate."""
        rec = StrategyRecommendation(
            recommended_strategy="chain-of-thought",
            confidence_boost=0.15,
            evidence_count=10,
        )
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought"],
            blocked_strategies={"chain-of-thought"},
            confidence=0.5,
            strategy_override=None,
            trace_id="test",
            data_recommendation=rec,
        )
        # Should fall through to confidence gate → fallback
        assert result == "auto"

    def test_no_boost_skips_recommendation(self):
        """Zero confidence boost means weak signal — skip recommendation."""
        rec = StrategyRecommendation(
            recommended_strategy="chain-of-thought",
            confidence_boost=0.0,
            evidence_count=10,
        )
        result = resolve_effective_strategy(
            selected_strategy="auto",
            available=["auto", "chain-of-thought"],
            blocked_strategies=set(),
            confidence=0.5,
            strategy_override=None,
            trace_id="test",
            data_recommendation=rec,
        )
        # No boost → falls through to confidence gate → fallback
        assert result == "auto"
