"""Tests for few-shot example retrieval."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.pattern_injection import (
    FewShotExample,
    _truncate_example,
    format_few_shot_examples,
    retrieve_few_shot_examples,
)


def _rand_emb(seed: int = 42, dim: int = 768) -> np.ndarray:
    rng = np.random.RandomState(seed)
    vec = rng.randn(dim).astype(np.float32)
    return vec / np.linalg.norm(vec)


class TestRetrieveFewShotExamples:
    """retrieve_few_shot_examples: similarity-based example retrieval."""

    @pytest.mark.asyncio
    async def test_cold_start_returns_empty(self, db_session):
        """Empty DB returns empty list."""
        result = await retrieve_few_shot_examples(
            raw_prompt="Write a REST API",
            db=db_session,
            trace_id="test",
            prompt_embedding=_rand_emb(1),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_low_scores_filtered_out(self, db_session):
        """Optimizations below min_score are excluded."""
        from app.models import Optimization

        base_emb = _rand_emb(42)
        for i in range(5):
            db_session.add(Optimization(
                raw_prompt=f"Test prompt {i}",
                optimized_prompt=f"Optimized {i}",
                status="completed",
                overall_score=5.0,  # below FEW_SHOT_MIN_SCORE (7.5)
                embedding=base_emb.tobytes(),
            ))
        await db_session.commit()

        result = await retrieve_few_shot_examples(
            raw_prompt="Test prompt",
            db=db_session,
            trace_id="test",
            prompt_embedding=base_emb,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_dissimilar_prompts_filtered(self, db_session):
        """Optimizations with low cosine similarity are excluded."""
        from app.models import Optimization

        # Use orthogonal embeddings
        db_session.add(Optimization(
            raw_prompt="Completely different topic",
            optimized_prompt="Optimized version",
            status="completed",
            overall_score=9.0,
            embedding=_rand_emb(999).tobytes(),
        ))
        await db_session.commit()

        result = await retrieve_few_shot_examples(
            raw_prompt="Write a REST API",
            db=db_session,
            trace_id="test",
            prompt_embedding=_rand_emb(1),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_top_examples_by_similarity(self, db_session):
        """Returns most similar examples first."""
        from app.models import Optimization

        base_emb = _rand_emb(42)
        for i in range(5):
            db_session.add(Optimization(
                raw_prompt=f"API endpoint {i}",
                optimized_prompt=f"Optimized API {i}",
                status="completed",
                overall_score=8.0,
                embedding=base_emb.tobytes(),
            ))
        await db_session.commit()

        result = await retrieve_few_shot_examples(
            raw_prompt="API endpoint",
            db=db_session,
            trace_id="test",
            prompt_embedding=base_emb,
        )
        assert len(result) <= 2  # FEW_SHOT_MAX_EXAMPLES
        assert all(isinstance(ex, FewShotExample) for ex in result)

    @pytest.mark.asyncio
    async def test_skips_failed_status(self, db_session):
        """Failed optimizations are excluded."""
        from app.models import Optimization

        base_emb = _rand_emb(42)
        db_session.add(Optimization(
            raw_prompt="Test",
            optimized_prompt="Optimized",
            status="failed",
            overall_score=9.0,
            embedding=base_emb.tobytes(),
        ))
        await db_session.commit()

        result = await retrieve_few_shot_examples(
            raw_prompt="Test",
            db=db_session,
            trace_id="test",
            prompt_embedding=base_emb,
        )
        assert result == []


class TestFormatFewShotExamples:
    """format_few_shot_examples: XML formatting."""

    def test_empty_returns_none(self):
        assert format_few_shot_examples([]) is None

    def test_formats_xml_structure(self):
        examples = [FewShotExample(
            raw_prompt="Write code",
            optimized_prompt="## Task\nWrite clean code",
            strategy_used="auto",
            overall_score=8.5,
            similarity=0.82,
            task_type="coding",
        )]
        result = format_few_shot_examples(examples)
        assert result is not None
        assert "<example-1" in result
        assert 'score="8.5"' in result
        assert 'strategy="auto"' in result
        assert "<before>" in result
        assert "<after>" in result

    def test_multiple_examples_numbered(self):
        examples = [
            FewShotExample("a", "b", "auto", 8.0, 0.9, "coding"),
            FewShotExample("c", "d", "auto", 7.5, 0.8, "coding"),
        ]
        result = format_few_shot_examples(examples)
        assert "<example-1" in result
        assert "<example-2" in result


class TestTruncateExample:
    """_truncate_example: budget-aware truncation."""

    def test_short_text_unchanged(self):
        raw, opt = _truncate_example("short", "also short", 2000)
        assert raw == "short"
        assert opt == "also short"

    def test_long_text_truncated(self):
        raw = "x" * 5000
        opt = "y" * 5000
        raw_out, opt_out = _truncate_example(raw, opt, 2000)
        assert len(raw_out) + len(opt_out) <= 2000 + 30  # truncation markers
        assert "truncated" in raw_out
        assert "truncated" in opt_out

    def test_budget_split_favors_optimized(self):
        raw = "x" * 5000
        opt = "y" * 5000
        raw_out, opt_out = _truncate_example(raw, opt, 2000)
        # 40% raw, 60% optimized
        assert len(raw_out) < len(opt_out)
