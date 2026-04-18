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
from collections.abc import AsyncGenerator
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio

from app.providers.base import LLMProvider
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.batch_pipeline import (
    PendingOptimization,
    bulk_persist,
    run_single_prompt,
)
from app.services.context_enrichment import EnrichedContext
from app.services.heuristic_analyzer import HeuristicAnalysis
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


# ---------------------------------------------------------------------------
# Tests — Fix 2/3: ContextEnrichmentService integration + divergence alerts
# ---------------------------------------------------------------------------


def _heuristic() -> HeuristicAnalysis:
    return HeuristicAnalysis(
        task_type="coding",
        domain="backend",
        intent_label="sort integers",
        confidence=0.85,
    )


def _enriched(**overrides: Any) -> EnrichedContext:
    """Build an EnrichedContext with sentinel text in each layer.

    Tests assert the sentinel strings reach the optimize render call.
    """
    defaults: dict[str, Any] = dict(
        raw_prompt="Write a function that sorts a list",
        codebase_context="CODEBASE_SENTINEL",
        strategy_intelligence="STRATEGY_SENTINEL",
        applied_patterns="PATTERNS_SENTINEL",
        analysis=_heuristic(),
        context_sources=MappingProxyType({
            "codebase_context": True,
            "strategy_intelligence": True,
            "applied_patterns": True,
            "heuristic_analysis": True,
        }),
        enrichment_meta=MappingProxyType({
            "enrichment_profile": "code_aware",
        }),
    )
    defaults.update(overrides)
    return EnrichedContext(**defaults)


def _enriched_with_divergences() -> EnrichedContext:
    """EnrichedContext whose `divergence_alerts` property renders alert text."""
    return _enriched(
        enrichment_meta=MappingProxyType({
            "enrichment_profile": "code_aware",
            "divergences": [
                {
                    "prompt_tech": "FastAPI",
                    "codebase_tech": "Flask",
                    "category": "web framework",
                },
            ],
        }),
    )


@pytest.fixture
def mock_context_service() -> AsyncMock:
    """Context service whose `enrich()` returns a sentinel-rich EnrichedContext."""
    svc = AsyncMock()
    svc.enrich = AsyncMock(return_value=_enriched())
    return svc


@pytest.fixture
def mock_session_factory() -> Any:
    """Minimal async context-manager factory that yields an AsyncMock session.

    The mocked context service ignores the db, so a no-op session is fine.
    """
    class _MockSession:
        async def __aenter__(self) -> AsyncMock:
            return AsyncMock()
        async def __aexit__(self, *_: Any) -> None:
            return None

    def _factory() -> _MockSession:
        return _MockSession()

    return _factory


class TestContextEnrichmentIntegration:
    """run_single_prompt must call ContextEnrichmentService.enrich() exactly
    once per prompt and thread its outputs into the optimize render."""

    async def test_enrich_called_once_per_prompt(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
        mock_context_service: AsyncMock,
        mock_session_factory: Any,
    ) -> None:
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        result: PendingOptimization = await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="internal",
            context_service=mock_context_service,
            session_factory=mock_session_factory,
        )

        assert result.status == "completed", f"run failed: {result.error}"
        assert mock_context_service.enrich.await_count == 1
        # Verify enrichment was invoked with the resolved tier
        _, kwargs = mock_context_service.enrich.call_args
        assert kwargs["tier"] == "internal"
        assert kwargs["raw_prompt"] == "Write a function that sorts a list"

    async def test_enrichment_layers_reach_optimize_render(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
        mock_context_service: AsyncMock,
        mock_session_factory: Any,
    ) -> None:
        """Codebase, strategy intel, and applied patterns from enrich() must
        end up in the rendered optimize.md payload sent to the provider."""
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="internal",
            context_service=mock_context_service,
            session_factory=mock_session_factory,
        )

        # Phase 2 (optimize) is the 2nd provider call (idx 1)
        optimize_call = mock_provider.complete_parsed.call_args_list[1]
        user_message = optimize_call.kwargs["user_message"]
        assert "CODEBASE_SENTINEL" in user_message
        assert "STRATEGY_SENTINEL" in user_message
        assert "PATTERNS_SENTINEL" in user_message

    async def test_divergence_alerts_reach_optimize_render(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
        mock_session_factory: Any,
    ) -> None:
        """The EnrichedContext.divergence_alerts property must be injected
        into the optimize template so the optimizer LLM sees tech conflicts.

        This is the fix for Divergence #3 (hardcoded None at batch_pipeline.py:281).
        """
        ctx_svc = AsyncMock()
        ctx_svc.enrich = AsyncMock(return_value=_enriched_with_divergences())

        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        await run_single_prompt(
            raw_prompt="Rewrite this FastAPI endpoint",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="internal",
            context_service=ctx_svc,
            session_factory=mock_session_factory,
        )

        optimize_call = mock_provider.complete_parsed.call_args_list[1]
        user_message = optimize_call.kwargs["user_message"]
        # The divergence_alerts property emits this header as its first line
        assert "TECHNOLOGY DIVERGENCE DETECTED" in user_message
        assert "FastAPI" in user_message
        assert "Flask" in user_message

    async def test_context_sources_persisted_on_pending(
        self,
        mock_provider: AsyncMock,
        prompt_loader: PromptLoader,
        mock_embedding_service: AsyncMock,
        mock_context_service: AsyncMock,
        mock_session_factory: Any,
    ) -> None:
        """enrichment.context_sources must be merged into PendingOptimization
        so bulk_persist writes the full context_sources dict (enrichment profile,
        divergences, layer flags) to the Optimization row."""
        mock_provider.complete_parsed.side_effect = [
            _analysis(), _optimization(), _scores(), _suggestions(),
        ]

        result: PendingOptimization = await run_single_prompt(
            raw_prompt="Write a function that sorts a list",
            provider=mock_provider,
            prompt_loader=prompt_loader,
            embedding_service=mock_embedding_service,
            tier="internal",
            context_service=mock_context_service,
            session_factory=mock_session_factory,
        )

        assert result.status == "completed", f"run failed: {result.error}"
        assert result.context_sources is not None
        # Layer flags from enrichment are present
        assert result.context_sources.get("codebase_context") is True
        assert result.context_sources.get("strategy_intelligence") is True
        # Enrichment profile from enrichment_meta is preserved
        assert (
            result.context_sources.get("enrichment_meta", {}).get("enrichment_profile")
            == "code_aware"
        )


# ---------------------------------------------------------------------------
# Tests — Fix 4: per-prompt optimization_created emission from bulk_persist
# ---------------------------------------------------------------------------


def _pending(
    *,
    pid: str = "opt-1",
    trace_id: str = "trace-1",
    batch_id: str = "batch-1",
    overall_score: float = 7.5,
    routing_tier: str = "internal",
    task_type: str = "coding",
    intent_label: str = "sort list",
    domain: str = "backend",
    domain_raw: str = "backend",
    strategy_used: str = "chain-of-thought",
    provider: str = "mock",
) -> PendingOptimization:
    """Build a minimal completed PendingOptimization suitable for bulk_persist."""
    return PendingOptimization(
        id=pid,
        trace_id=trace_id,
        raw_prompt="Write a function that sorts a list",
        batch_id=batch_id,
        optimized_prompt="Write a Python function...",
        task_type=task_type,
        strategy_used=strategy_used,
        changes_summary="Added specificity.",
        score_clarity=7.0,
        score_specificity=8.0,
        score_structure=7.0,
        score_faithfulness=8.0,
        score_conciseness=7.0,
        overall_score=overall_score,
        improvement_score=2.0,
        scoring_mode="hybrid",
        intent_label=intent_label,
        domain=domain,
        domain_raw=domain_raw,
        embedding=None,
        models_by_phase={"analyze": "haiku", "optimize": "sonnet", "score": "haiku"},
        original_scores={"clarity": 5.0},
        score_deltas={"clarity": 2.0},
        duration_ms=1200,
        status="completed",
        provider=provider,
        model_used="sonnet",
        routing_tier=routing_tier,
        heuristic_flags=[],
        suggestions=[],
        context_sources={"batch_id": batch_id, "source": "batch_seed"},
    )


@pytest_asyncio.fixture
async def real_session_factory() -> AsyncGenerator[Any, None]:
    """In-memory sqlite factory that creates a fresh session per call.

    bulk_persist() uses `async with session_factory() as db:` — the factory
    must return a fresh session each invocation so transaction boundaries
    match production behavior.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class TestBulkPersistEvents:
    """bulk_persist must emit an `optimization_created` event for every row
    it actually inserts — matching the regular pipeline's contract so the
    frontend history refresh and cross-process MCP bridge fire reliably."""

    async def test_emits_one_event_per_inserted_row(
        self, real_session_factory: Any,
    ) -> None:
        """Subscribe a queue to the event bus and assert N events fire for
        N completed + quality-passing rows.
        """
        from app.services.event_bus import event_bus

        pendings = [
            _pending(pid="opt-a", trace_id="trace-a", batch_id="batch-1"),
            _pending(pid="opt-b", trace_id="trace-b", batch_id="batch-1"),
            _pending(pid="opt-c", trace_id="trace-c", batch_id="batch-1"),
        ]

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        try:
            inserted = await bulk_persist(
                results=pendings,
                session_factory=real_session_factory,
                batch_id="batch-1",
            )
        finally:
            event_bus._subscribers.discard(queue)

        assert inserted == 3

        # Drain captured events
        captured: list[dict] = []
        while not queue.empty():
            captured.append(queue.get_nowait())

        opt_events = [e for e in captured if e["event"] == "optimization_created"]
        assert len(opt_events) == 3, (
            f"Expected 3 optimization_created events, got {len(opt_events)}: "
            f"{[e['event'] for e in captured]}"
        )

        # Event data must carry the key fields consumers rely on
        ids = {e["data"]["id"] for e in opt_events}
        assert ids == {"opt-a", "opt-b", "opt-c"}

        for evt in opt_events:
            data = evt["data"]
            assert data["batch_id"] == "batch-1"
            assert data["source"] == "batch_seed"
            assert data["routing_tier"] == "internal"
            assert data["task_type"] == "coding"
            assert data["status"] == "completed"
            assert data["strategy_used"] == "chain-of-thought"
            assert data["provider"] == "mock"
            assert data["intent_label"] == "sort list"
            assert data["domain"] == "backend"
            assert data["overall_score"] == 7.5

    async def test_no_event_for_quality_rejected_rows(
        self, real_session_factory: Any,
    ) -> None:
        """Rows rejected by the quality gate (score < 5.0) must NOT emit
        an optimization_created event — they never reach the DB."""
        from app.services.event_bus import event_bus

        # Two rejected, one accepted
        pendings = [
            _pending(
                pid="opt-low1", trace_id="trace-1",
                batch_id="batch-2", overall_score=3.0,
            ),
            _pending(
                pid="opt-low2", trace_id="trace-2",
                batch_id="batch-2", overall_score=4.9,
            ),
            _pending(
                pid="opt-ok", trace_id="trace-3",
                batch_id="batch-2", overall_score=7.5,
            ),
        ]

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        try:
            inserted = await bulk_persist(
                results=pendings,
                session_factory=real_session_factory,
                batch_id="batch-2",
            )
        finally:
            event_bus._subscribers.discard(queue)

        assert inserted == 1

        captured: list[dict] = []
        while not queue.empty():
            captured.append(queue.get_nowait())
        opt_events = [e for e in captured if e["event"] == "optimization_created"]
        assert len(opt_events) == 1
        assert opt_events[0]["data"]["id"] == "opt-ok"

    async def test_idempotency_skips_re_emission(
        self, real_session_factory: Any,
    ) -> None:
        """A second bulk_persist call for the same batch_id must skip
        already-persisted rows — no duplicate events."""
        from app.services.event_bus import event_bus

        pendings = [
            _pending(pid="opt-dup", trace_id="trace-dup", batch_id="batch-3"),
        ]

        # First insert
        inserted_1 = await bulk_persist(
            results=pendings,
            session_factory=real_session_factory,
            batch_id="batch-3",
        )
        assert inserted_1 == 1

        # Second call — should be idempotent
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        event_bus._subscribers.add(queue)
        try:
            inserted_2 = await bulk_persist(
                results=pendings,
                session_factory=real_session_factory,
                batch_id="batch-3",
            )
        finally:
            event_bus._subscribers.discard(queue)

        assert inserted_2 == 0

        captured: list[dict] = []
        while not queue.empty():
            captured.append(queue.get_nowait())
        opt_events = [e for e in captured if e["event"] == "optimization_created"]
        assert len(opt_events) == 0, (
            "Idempotent second run must not re-emit optimization_created"
        )
