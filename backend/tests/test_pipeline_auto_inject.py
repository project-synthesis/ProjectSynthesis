"""Tests for pipeline auto-injection pre-phase.

Covers the _auto_inject_patterns() helper and its integration into the
pipeline's optimize phase.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.services.pattern_injection import InjectedPattern
from app.services.pipeline import PipelineOrchestrator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_emb(dim: int = 384) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _make_meta_pattern(cluster_id: str, text: str):
    mp = MagicMock()
    mp.cluster_id = cluster_id
    mp.pattern_text = text
    return mp


def _make_taxonomy_engine(size: int = 1, matches=None):
    """Build a minimal taxonomy_engine mock.

    ``matches`` is the list returned by embedding_index.search().
    """
    embedding_index = MagicMock()
    embedding_index.size = size
    # search() is synchronous
    embedding_index.search = MagicMock(return_value=matches if matches is not None else [])

    engine = MagicMock()
    engine.embedding_index = embedding_index
    return engine


# ---------------------------------------------------------------------------
# Unit tests for _auto_inject_patterns()
# ---------------------------------------------------------------------------

class TestAutoInjectPatterns:
    @pytest.fixture
    def orchestrator(self, tmp_path):
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "strategies").mkdir()
        (prompts / "agent-guidance.md").write_text("System prompt.")
        (prompts / "analyze.md").write_text("{{raw_prompt}}\n{{available_strategies}}")
        (prompts / "optimize.md").write_text(
            "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}"
        )
        (prompts / "scoring.md").write_text("Score.")
        (prompts / "manifest.json").write_text(
            '{"analyze.md": {"required": ["raw_prompt", "available_strategies"], "optional": []},'
            '"optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"], "optional": []},'
            '"scoring.md": {"required": [], "optional": []}}'
        )
        (prompts / "strategies" / "auto.md").write_text("Auto-select.")
        return PipelineOrchestrator(prompts_dir=prompts)

    async def test_returns_patterns_when_matches_found(self, orchestrator, db_session):
        """When index finds matches and DB has MetaPatterns, returns InjectedPattern objects."""
        cluster_id = "cluster-abc"
        mp = _make_meta_pattern(cluster_id, "Use concise verbs in prompts")
        engine = _make_taxonomy_engine(
            size=1,
            matches=[(cluster_id, 0.85)],
        )

        fake_embedding = _rand_emb()

        # Fusion signal: pattern query (1 DB call — output signal now uses
        # OptimizedEmbeddingIndex instead of a DB query)
        mock_fusion_result = MagicMock()
        mock_fusion_result.scalar_one_or_none.return_value = None
        mock_fusion_result.all.return_value = []
        mock_fusion_result.scalars.return_value.all.return_value = []

        # Topic-match execute call: PromptCluster metadata query
        cluster_row = MagicMock()
        cluster_row.id = cluster_id
        cluster_row.label = "Verb Patterns"
        cluster_row.domain = "writing"
        mock_cluster_result = MagicMock()
        mock_cluster_result.__iter__ = MagicMock(return_value=iter([cluster_row]))

        # Topic-match execute call: MetaPattern query
        mock_pattern_result = MagicMock()
        mock_pattern_result.scalars.return_value.all.return_value = [mp]

        # Sub-domain parent lookup: parent_q returns the parent_id
        mock_parent_result = MagicMock()
        mock_parent_result.all.return_value = []  # no sub-domain parents

        db_session.execute = AsyncMock(
            side_effect=[mock_fusion_result, mock_cluster_result, mock_parent_result, mock_pattern_result]
        )

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=fake_embedding),
        ):
            patterns, ids = await orchestrator._auto_inject_patterns(
                raw_prompt="Write a function to sort a list",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-001",
            )

        assert len(patterns) == 1
        assert isinstance(patterns[0], InjectedPattern)
        assert patterns[0].pattern_text == "Use concise verbs in prompts"
        assert patterns[0].cluster_label == "Verb Patterns"
        assert patterns[0].domain == "writing"
        assert patterns[0].similarity == 0.85
        assert ids == [cluster_id]

    async def test_returns_empty_when_index_is_empty(self, orchestrator, db_session):
        """When embedding index has size==0, returns empty lists immediately."""
        engine = _make_taxonomy_engine(size=0, matches=[])

        texts, ids = await orchestrator._auto_inject_patterns(
            raw_prompt="Write a function",
            taxonomy_engine=engine,
            db=db_session,
            trace_id="trace-002",
        )

        assert texts == []
        assert ids == []
        # search() should never be called if size == 0
        engine.embedding_index.search.assert_not_called()

    async def test_returns_empty_when_no_matches_above_threshold(self, orchestrator, db_session):
        """When cosine search returns no matches, returns empty lists."""
        engine = _make_taxonomy_engine(size=5, matches=[])

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=_rand_emb()),
        ):
            texts, ids = await orchestrator._auto_inject_patterns(
                raw_prompt="Write a function",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-003",
            )

        assert texts == []
        assert ids == []

    async def test_returns_empty_when_no_db_meta_patterns(self, orchestrator, db_session):
        """When matches found but no MetaPatterns in DB, returns ([], []) — no contributing clusters."""
        cluster_id = "cluster-xyz"
        engine = _make_taxonomy_engine(size=1, matches=[(cluster_id, 0.80)])

        # Fusion signal: pattern query only (output signal uses index, not DB)
        mock_fusion_result = MagicMock()
        mock_fusion_result.scalar_one_or_none.return_value = None
        mock_fusion_result.all.return_value = []
        mock_fusion_result.scalars.return_value.all.return_value = []

        # Topic-match execute call: PromptCluster metadata query
        cluster_row = MagicMock()
        cluster_row.id = cluster_id
        cluster_row.label = "Some Cluster"
        cluster_row.domain = "general"
        mock_cluster_result = MagicMock()
        mock_cluster_result.__iter__ = MagicMock(return_value=iter([cluster_row]))

        # Sub-domain parent lookup: no parents
        mock_parent_result = MagicMock()
        mock_parent_result.all.return_value = []

        # Topic-match execute call: MetaPattern query — no patterns
        mock_pattern_result = MagicMock()
        mock_pattern_result.scalars.return_value.all.return_value = []

        db_session.execute = AsyncMock(
            side_effect=[mock_fusion_result, mock_cluster_result, mock_parent_result, mock_pattern_result]
        )

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=_rand_emb()),
        ):
            patterns, ids = await orchestrator._auto_inject_patterns(
                raw_prompt="Write something",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-004",
            )

        assert patterns == []
        # No MetaPatterns → no contributing clusters (only IDs with actual patterns returned)
        assert ids == []


# ---------------------------------------------------------------------------
# Integration-style tests — verify auto-injection wires into pipeline.run()
# ---------------------------------------------------------------------------

class TestPipelineAutoInjectionIntegration:
    """Test that auto-injection is correctly wired into the pipeline."""

    @pytest.fixture
    def orchestrator(self, tmp_path):
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        strategies = prompts / "strategies"
        strategies.mkdir()
        (prompts / "agent-guidance.md").write_text("System prompt.")
        (prompts / "analyze.md").write_text("{{raw_prompt}}\n{{available_strategies}}")
        (prompts / "optimize.md").write_text(
            "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}\n"
            "{{applied_patterns}}"
        )
        (prompts / "scoring.md").write_text("Score.")
        (prompts / "manifest.json").write_text(
            '{"analyze.md": {"required": ["raw_prompt", "available_strategies"], "optional": []},'
            '"optimize.md": {"required": ["raw_prompt", "strategy_instructions", "analysis_summary"],'
            '"optional": ["codebase_guidance", "codebase_context", "strategy_intelligence", "applied_patterns"]},'
            '"scoring.md": {"required": [], "optional": []}}'
        )
        (strategies / "auto.md").write_text("Auto-select.")
        (strategies / "chain-of-thought.md").write_text("Think step by step.")
        return PipelineOrchestrator(prompts_dir=prompts)

    def _make_analysis(self):
        from app.schemas.pipeline_contracts import AnalysisResult
        return AnalysisResult(
            task_type="coding",
            weaknesses=["vague"],
            strengths=["concise"],
            selected_strategy="chain-of-thought",
            strategy_rationale="good",
            confidence=0.9,
        )

    def _make_optimization(self):
        from app.schemas.pipeline_contracts import OptimizationResult
        return OptimizationResult(
            optimized_prompt="Write a Python function that sorts a list.",
            changes_summary="Added specificity.",
        )

    def _make_scores(self):
        from app.schemas.pipeline_contracts import DimensionScores, ScoreResult
        return ScoreResult(
            prompt_a_scores=DimensionScores(
                clarity=4.0, specificity=3.0, structure=5.0, faithfulness=5.0, conciseness=6.0,
            ),
            prompt_b_scores=DimensionScores(
                clarity=8.0, specificity=8.0, structure=7.0, faithfulness=9.0, conciseness=7.0,
            ),
        )

    async def test_context_injected_event_emitted_when_patterns_found(
        self, orchestrator, mock_provider, db_session
    ):
        """When auto-injection finds patterns, a context_injected SSE event is emitted."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]

        cluster_id = "cluster-001"
        mp = _make_meta_pattern(cluster_id, "Be explicit about return types")
        engine = _make_taxonomy_engine(size=1, matches=[(cluster_id, 0.88)])
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mp]
        db_session.execute = AsyncMock(return_value=mock_result)

        injected_pattern = InjectedPattern(
            pattern_text="Be explicit about return types",
            cluster_label="Return Types",
            domain="coding",
            similarity=0.88,
        )

        events = []
        with (
            patch(
                "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
                new=AsyncMock(return_value=([injected_pattern], [cluster_id])),
            ),
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
            ):
                events.append(event)

        event_names = [e.event for e in events]
        assert "context_injected" in event_names

        injected = next(e for e in events if e.event == "context_injected")
        assert injected.data["clusters"] == [cluster_id]
        assert injected.data["patterns"] == 1

    async def test_context_injected_not_emitted_when_no_patterns(
        self, orchestrator, mock_provider, db_session
    ):
        """When auto-injection finds nothing, no context_injected event is emitted."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        engine = _make_taxonomy_engine(size=0, matches=[])

        events = []
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=AsyncMock(return_value=([], [])),
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
            ):
                events.append(event)

        event_names = [e.event for e in events]
        assert "context_injected" not in event_names

    async def test_auto_injection_runs_alongside_explicit_pattern_ids(
        self, orchestrator, mock_provider, db_session
    ):
        """When user provides explicit applied_pattern_ids, auto-injection still runs."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        engine = _make_taxonomy_engine(size=5, matches=[("c1", 0.9)])

        injected_pattern = InjectedPattern(
            pattern_text="some pattern",
            cluster_label="Test",
            domain="general",
            similarity=0.9,
        )

        events = []
        inject_mock = AsyncMock(return_value=([injected_pattern], ["c1"]))
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=inject_mock,
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
                applied_pattern_ids=["explicit-pattern-id"],
            ):
                events.append(event)

        # Auto-injection runs even with explicit patterns — they are merged
        inject_mock.assert_called_once()

    async def test_cluster_injection_added_to_context_sources(
        self, orchestrator, mock_provider, db_session
    ):
        """When patterns are injected, context_sources['cluster_injection'] is True."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        cluster_id = "cluster-007"
        engine = _make_taxonomy_engine(size=1, matches=[(cluster_id, 0.82)])

        injected_pattern = InjectedPattern(
            pattern_text="Use numbered steps",
            cluster_label="Step Patterns",
            domain="writing",
            similarity=0.82,
        )

        events = []
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=AsyncMock(return_value=([injected_pattern], [cluster_id])),
        ):
            async for event in orchestrator.run(
                raw_prompt="Explain how to make coffee",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
            ):
                events.append(event)

        complete = next(e for e in events if e.event == "optimization_complete")
        assert complete.data["context_sources"].get("cluster_injection") is True

    async def test_auto_injection_failure_does_not_abort_pipeline(
        self, orchestrator, mock_provider, db_session
    ):
        """A failing auto-injection is swallowed; pipeline completes normally."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        engine = _make_taxonomy_engine(size=1, matches=[("c1", 0.9)])

        events = []
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=AsyncMock(side_effect=RuntimeError("embedding service down")),
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
            ):
                events.append(event)

        event_names = [e.event for e in events]
        assert "optimization_complete" in event_names
        assert "error" not in event_names

    async def test_injection_stats_persisted_to_enrichment_meta(
        self, orchestrator, mock_provider, db_session
    ):
        """UI1: injection_stats must land in enrichment_meta so the Inspector
        can render patterns_injected / injection_clusters / has_explicit_patterns.
        Currently these are only emitted to per-phase JSONL traces."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        cluster_id = "cluster-042"
        engine = _make_taxonomy_engine(size=1, matches=[(cluster_id, 0.91)])

        injected = InjectedPattern(
            pattern_text="Include explicit examples",
            cluster_label="Examples",
            domain="coding",
            similarity=0.91,
        )

        events = []
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=AsyncMock(return_value=([injected], [cluster_id])),
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
                applied_pattern_ids=["explicit-pattern-99"],
            ):
                events.append(event)

        complete = next(e for e in events if e.event == "optimization_complete")
        em = complete.data["context_sources"]["enrichment_meta"]
        assert "injection_stats" in em, (
            "enrichment_meta must carry injection_stats for Inspector rendering"
        )
        stats = em["injection_stats"]
        assert stats["patterns_injected"] == 1
        assert stats["injection_clusters"] == 1
        assert stats["has_explicit_patterns"] is True

    async def test_injection_stats_present_even_with_zero_injected(
        self, orchestrator, mock_provider, db_session
    ):
        """UI1: injection_stats is still written when nothing was injected so
        the UI can show '0 patterns / 0 clusters' truthfully rather than
        hiding the row (ambiguous: did injection fire and miss, or not run?)."""
        mock_provider.complete_parsed.side_effect = [
            self._make_analysis(),
            self._make_optimization(),
            self._make_scores(),
        ]
        engine = _make_taxonomy_engine(size=0, matches=[])

        events = []
        with patch(
            "app.services.pipeline.PipelineOrchestrator._auto_inject_patterns",
            new=AsyncMock(return_value=([], [])),
        ):
            async for event in orchestrator.run(
                raw_prompt="Write a sort function",
                provider=mock_provider,
                db=db_session,
                taxonomy_engine=engine,
            ):
                events.append(event)

        complete = next(e for e in events if e.event == "optimization_complete")
        em = complete.data["context_sources"]["enrichment_meta"]
        assert "injection_stats" in em
        stats = em["injection_stats"]
        assert stats["patterns_injected"] == 0
        assert stats["injection_clusters"] == 0
        assert stats["has_explicit_patterns"] is False
