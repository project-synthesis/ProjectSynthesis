"""Tests for probe_generation.generate_probe_prompts (Topic Probe Tier 1).

AC-C3-1 through AC-C3-5 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 3.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.providers.base import LLMProvider
from app.services.probe_generation import (
    ProbeContext,
    ProbeGenerationError,
    generate_probe_prompts,
)


def _make_ctx(**overrides) -> ProbeContext:
    base = dict(
        topic="embedding cache invalidation",
        scope="**/*",
        intent_hint="audit",
        repo_full_name="owner/repo",
        project_id=None,
        project_name="repo",
        dominant_stack=["python"],
        relevant_files=["backend/app/services/taxonomy/embedding_index.py"],
        explore_synthesis_excerpt="(synthesis snippet)",
        known_domains=["backend"],
        existing_clusters_brief=[{"label": "Embedding Index Audits"}],
    )
    base.update(overrides)
    return ProbeContext(**base)


class TestProbeGeneration:
    @pytest.mark.asyncio
    async def test_happy_path_returns_n_prompts(self, monkeypatch):
        """AC-C3-1: returns parsed list[str] of length n_prompts (with backticks)."""
        provider = AsyncMock(spec=LLMProvider)
        provider.complete_parsed = AsyncMock(return_value=MagicMock(
            prompts=[
                f"Audit `EmbeddingIndex.add` for cache invalidation correctness ({i})"
                for i in range(12)
            ],
        ))
        ctx = _make_ctx()
        prompts = await generate_probe_prompts(ctx, provider=provider, n_prompts=12)
        assert len(prompts) == 12
        for p in prompts:
            assert "`" in p

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self, monkeypatch):
        """AC-C3-2: call_provider_with_retry retries on transient failure."""
        provider = AsyncMock(spec=LLMProvider)
        # Two transient failures, then success
        provider.complete_parsed = AsyncMock(side_effect=[
            ConnectionError("transient 1"),
            ConnectionError("transient 2"),
            MagicMock(prompts=[f"Audit `mod_{i}.py` for X" for i in range(12)]),
        ])
        ctx = _make_ctx()
        prompts = await generate_probe_prompts(ctx, provider=provider, n_prompts=12)
        assert len(prompts) == 12
        assert provider.complete_parsed.call_count == 3

    @pytest.mark.asyncio
    async def test_drops_prompts_without_backticks(self):
        """AC-C3-3: prompts without backtick identifiers are dropped; >50% drops raises."""
        provider = AsyncMock(spec=LLMProvider)
        # 8 of 12 are valid (backticked); 4 are bare prose — under 50% drop threshold
        prompts_response = [
            f"Audit `module_{i}.py`" if i < 8 else f"Generic prompt {i} no backticks"
            for i in range(12)
        ]
        provider.complete_parsed = AsyncMock(return_value=MagicMock(prompts=prompts_response))
        ctx = _make_ctx()
        prompts = await generate_probe_prompts(ctx, provider=provider, n_prompts=12)
        assert len(prompts) == 8  # 4 dropped
        for p in prompts:
            assert "`" in p

    @pytest.mark.asyncio
    async def test_raises_when_majority_dropped(self):
        """AC-C3-3: > 50% drop raises ProbeGenerationError."""
        provider = AsyncMock(spec=LLMProvider)
        # 3 of 12 valid → 9 dropped (75%) → raise
        prompts_response = [
            f"Audit `mod_{i}.py`" if i < 3 else f"No backticks {i}"
            for i in range(12)
        ]
        provider.complete_parsed = AsyncMock(return_value=MagicMock(prompts=prompts_response))
        ctx = _make_ctx()
        with pytest.raises(ProbeGenerationError, match=r"backtick"):
            await generate_probe_prompts(ctx, provider=provider, n_prompts=12)

    @pytest.mark.asyncio
    async def test_n_prompts_clamped_5_to_25(self):
        """AC-C3-4: n_prompts clamped to [5, 25] range."""
        provider = AsyncMock(spec=LLMProvider)
        provider.complete_parsed = AsyncMock(return_value=MagicMock(
            prompts=[f"Audit `m_{i}.py`" for i in range(25)],
        ))
        ctx = _make_ctx()
        # Below floor → clamped to 5
        prompts_low = await generate_probe_prompts(ctx, provider=provider, n_prompts=2)
        assert len(prompts_low) == 5
        # Above ceiling → clamped to 25
        prompts_high = await generate_probe_prompts(ctx, provider=provider, n_prompts=100)
        assert len(prompts_high) == 25

    @pytest.mark.asyncio
    async def test_uses_sonnet_model(self):
        """AC-C3-5: uses settings.MODEL_SONNET (long-context for codebase context)."""
        provider = AsyncMock(spec=LLMProvider)
        provider.complete_parsed = AsyncMock(return_value=MagicMock(
            prompts=[f"`x{i}`" for i in range(12)],
        ))
        ctx = _make_ctx()
        await generate_probe_prompts(ctx, provider=provider, n_prompts=12)
        called_kwargs = provider.complete_parsed.call_args.kwargs
        assert called_kwargs.get("model") == settings.MODEL_SONNET
