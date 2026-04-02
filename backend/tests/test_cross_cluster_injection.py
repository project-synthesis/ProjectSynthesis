"""Tests for cross-cluster pattern injection in auto_inject_patterns().

Verifies that universal patterns with high global_source_count are injected
even when topic-based matching finds nothing, that the relevance floor is
respected, and that deduplication prevents double-injection.

Copyright 2025-2026 Project Synthesis contributors.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import math
import numpy as np
import pytest

from app.services.pattern_injection import InjectedPattern, auto_inject_patterns


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand_emb(dim: int = 384) -> np.ndarray:
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _emb_bytes(emb: np.ndarray) -> bytes:
    """Convert a numpy embedding to bytes (as stored in MetaPattern.embedding)."""
    return emb.astype(np.float32).tobytes()


def _make_taxonomy_engine(size: int = 0, matches=None):
    """Build a minimal taxonomy_engine mock."""
    embedding_index = MagicMock()
    embedding_index.size = size
    embedding_index.search = MagicMock(return_value=matches if matches is not None else [])

    engine = MagicMock()
    engine.embedding_index = embedding_index
    return engine


def _make_cross_cluster_row(
    mp_id: str,
    cluster_id: str,
    pattern_text: str,
    global_source_count: int,
    embedding: bytes,
    cluster_label: str = "Universal",
    cluster_domain: str = "general",
    cluster_avg_score: float = 7.0,
):
    """Create a mock row matching the cross-cluster query shape:
    (MetaPattern, cluster_label, cluster_domain, cluster_avg_score).
    """
    mp = MagicMock()
    mp.id = mp_id
    mp.cluster_id = cluster_id
    mp.pattern_text = pattern_text
    mp.global_source_count = global_source_count
    mp.embedding = embedding
    return (mp, cluster_label, cluster_domain, cluster_avg_score)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrossClusterInjection:
    """Tests for the cross-cluster injection section of auto_inject_patterns."""

    @pytest.fixture
    def prompt_emb(self):
        """A deterministic prompt embedding."""
        return _rand_emb()

    async def test_cross_cluster_injected_when_topic_finds_nothing(self, db_session):
        """Cross-cluster patterns are included even when the embedding index is empty."""
        prompt_emb = _rand_emb()
        # Use a similar embedding so cosine similarity is high
        pat_emb = prompt_emb.copy()

        engine = _make_taxonomy_engine(size=0)  # empty index -> no topic matches

        cc_row = _make_cross_cluster_row(
            mp_id="mp-cc-1",
            cluster_id="cluster-cc-1",
            pattern_text="Use structured output format",
            global_source_count=5,
            embedding=_emb_bytes(pat_emb),
            cluster_label="Output Formatting",
            cluster_domain="writing",
            cluster_avg_score=8.0,
        )

        # The cross-cluster query is the third execute call (after potential
        # topic queries which are skipped when size==0).
        mock_cc_result = MagicMock()
        mock_cc_result.all.return_value = [cc_row]
        db_session.execute = AsyncMock(return_value=mock_cc_result)

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, cluster_ids = await auto_inject_patterns(
                raw_prompt="Write a function to format data",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-001",
            )

        # Should have cross-cluster patterns
        assert len(injected) >= 1
        cc_patterns = [p for p in injected if "(cross-cluster)" in p.cluster_label]
        assert len(cc_patterns) >= 1
        assert cc_patterns[0].pattern_text == "Use structured output format"
        assert cc_patterns[0].cluster_label == "Output Formatting (cross-cluster)"
        assert cc_patterns[0].domain == "writing"

    async def test_relevance_floor_filters_low_relevance(self, db_session):
        """Patterns with relevance below CROSS_CLUSTER_RELEVANCE_FLOOR are excluded."""
        prompt_emb = _rand_emb()
        # Create an embedding that's orthogonal -> low similarity
        orthogonal_emb = np.zeros(384, dtype=np.float32)
        orthogonal_emb[0] = 1.0  # unit vector in one dimension
        # Make prompt_emb orthogonal to it
        prompt_emb_orth = np.zeros(384, dtype=np.float32)
        prompt_emb_orth[1] = 1.0  # orthogonal to orthogonal_emb

        engine = _make_taxonomy_engine(size=0)

        cc_row = _make_cross_cluster_row(
            mp_id="mp-cc-low",
            cluster_id="cluster-cc-low",
            pattern_text="Some niche pattern",
            global_source_count=3,
            embedding=_emb_bytes(orthogonal_emb),
            cluster_label="Niche Cluster",
            cluster_domain="general",
            cluster_avg_score=5.0,
        )

        mock_cc_result = MagicMock()
        mock_cc_result.all.return_value = [cc_row]
        db_session.execute = AsyncMock(return_value=mock_cc_result)

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb_orth),
        ):
            injected, _ = await auto_inject_patterns(
                raw_prompt="Something completely different",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-002",
            )

        # The orthogonal embedding should produce near-zero similarity,
        # so relevance = ~0 * log2(1+3) * 0.5 = ~0, below the 0.35 floor.
        cc_patterns = [p for p in injected if "(cross-cluster)" in (p.cluster_label or "")]
        assert len(cc_patterns) == 0

    async def test_deduplication_with_topic_patterns(self, db_session):
        """Patterns already found via topic match are not re-injected as cross-cluster."""
        prompt_emb = _rand_emb()
        shared_mp_id = "mp-shared-001"
        cluster_id = "cluster-topic-1"

        engine = _make_taxonomy_engine(
            size=1,
            matches=[(cluster_id, 0.85)],
        )

        # Topic-match query mocks
        cluster_row = MagicMock()
        cluster_row.id = cluster_id
        cluster_row.label = "Topic Cluster"
        cluster_row.domain = "coding"
        mock_cluster_result = MagicMock()
        mock_cluster_result.__iter__ = MagicMock(return_value=iter([cluster_row]))

        topic_mp = MagicMock()
        topic_mp.id = shared_mp_id
        topic_mp.cluster_id = cluster_id
        topic_mp.pattern_text = "Use type annotations"
        mock_pattern_result = MagicMock()
        mock_pattern_result.scalars.return_value.all.return_value = [topic_mp]

        # Cross-cluster query returns the SAME pattern (same mp.id)
        cc_row = _make_cross_cluster_row(
            mp_id=shared_mp_id,  # same ID as topic pattern
            cluster_id=cluster_id,
            pattern_text="Use type annotations",
            global_source_count=10,
            embedding=_emb_bytes(prompt_emb),
            cluster_label="Topic Cluster",
            cluster_domain="coding",
            cluster_avg_score=8.0,
        )
        mock_cc_result = MagicMock()
        mock_cc_result.all.return_value = [cc_row]

        # Order: cluster metadata, meta-patterns (topic), cross-cluster
        db_session.execute = AsyncMock(
            side_effect=[mock_cluster_result, mock_pattern_result, mock_cc_result]
        )

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, cluster_ids_out = await auto_inject_patterns(
                raw_prompt="Write a typed Python function",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-003",
            )

        # The shared pattern should appear exactly once (from topic match),
        # NOT duplicated as cross-cluster.
        assert len(injected) == 1
        assert injected[0].pattern_text == "Use type annotations"
        assert "(cross-cluster)" not in injected[0].cluster_label

    async def test_relevance_formula_includes_cluster_avg_score(self, db_session):
        """The relevance formula uses cosine_sim * log2(1+count) * cluster_avg_score_factor."""
        prompt_emb = _rand_emb()
        # Use same embedding for high cosine similarity (~1.0)
        pat_emb = prompt_emb.copy()

        engine = _make_taxonomy_engine(size=0)

        global_count = 7
        avg_score = 8.0

        cc_row = _make_cross_cluster_row(
            mp_id="mp-formula",
            cluster_id="cluster-formula",
            pattern_text="Test formula pattern",
            global_source_count=global_count,
            embedding=_emb_bytes(pat_emb),
            cluster_label="Formula Test",
            cluster_domain="analysis",
            cluster_avg_score=avg_score,
        )

        mock_cc_result = MagicMock()
        mock_cc_result.all.return_value = [cc_row]
        db_session.execute = AsyncMock(return_value=mock_cc_result)

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, _ = await auto_inject_patterns(
                raw_prompt="Analyze this dataset",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-004",
            )

        # Verify the pattern was injected
        cc_patterns = [p for p in injected if "(cross-cluster)" in (p.cluster_label or "")]
        assert len(cc_patterns) == 1

        # Verify the similarity score matches the formula:
        # relevance = cosine_sim * log2(1 + global_count) * cluster_avg_score_factor
        # cosine_sim ~= 1.0 (same embedding), log2(1+7) = 3.0, factor = 8.0/10.0 = 0.8
        expected_relevance = 1.0 * math.log2(1 + global_count) * (avg_score / 10.0)
        assert abs(cc_patterns[0].similarity - round(expected_relevance, 2)) < 0.05

    async def test_cross_cluster_handles_none_avg_score(self, db_session):
        """When cluster.avg_score is None, defaults to 5.0/10.0 = 0.5 factor."""
        prompt_emb = _rand_emb()
        pat_emb = prompt_emb.copy()

        engine = _make_taxonomy_engine(size=0)

        cc_row = _make_cross_cluster_row(
            mp_id="mp-none-score",
            cluster_id="cluster-none-score",
            pattern_text="Pattern with null score",
            global_source_count=10,
            embedding=_emb_bytes(pat_emb),
            cluster_label="Null Score Cluster",
            cluster_domain="general",
            cluster_avg_score=None,
        )

        mock_cc_result = MagicMock()
        mock_cc_result.all.return_value = [cc_row]
        db_session.execute = AsyncMock(return_value=mock_cc_result)

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, _ = await auto_inject_patterns(
                raw_prompt="General prompt",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-005",
            )

        cc_patterns = [p for p in injected if "(cross-cluster)" in (p.cluster_label or "")]
        assert len(cc_patterns) == 1
        # With None avg_score -> default 5.0/10.0 = 0.5 factor
        # sim ~1.0, log2(1+10) ~= 3.459, factor = 0.5
        expected = round(1.0 * math.log2(11) * 0.5, 2)
        assert abs(cc_patterns[0].similarity - expected) < 0.05

    async def test_cross_cluster_exception_is_non_fatal(self, db_session):
        """If the cross-cluster query raises, the function returns topic results gracefully."""
        prompt_emb = _rand_emb()
        cluster_id = "cluster-topic-ok"

        engine = _make_taxonomy_engine(
            size=1,
            matches=[(cluster_id, 0.9)],
        )

        # Topic-match queries succeed
        cluster_row = MagicMock()
        cluster_row.id = cluster_id
        cluster_row.label = "Good Topic"
        cluster_row.domain = "coding"
        mock_cluster_result = MagicMock()
        mock_cluster_result.__iter__ = MagicMock(return_value=iter([cluster_row]))

        topic_mp = MagicMock()
        topic_mp.id = "mp-topic-ok"
        topic_mp.cluster_id = cluster_id
        topic_mp.pattern_text = "Topic pattern"
        mock_pattern_result = MagicMock()
        mock_pattern_result.scalars.return_value.all.return_value = [topic_mp]

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_cluster_result
            if call_count == 2:
                return mock_pattern_result
            # Third call (cross-cluster) raises
            raise RuntimeError("DB connection lost")

        db_session.execute = AsyncMock(side_effect=_side_effect)

        with patch(
            "app.services.embedding_service.EmbeddingService.aembed_single",
            new=AsyncMock(return_value=prompt_emb),
        ):
            injected, cluster_ids_out = await auto_inject_patterns(
                raw_prompt="Write a function",
                taxonomy_engine=engine,
                db=db_session,
                trace_id="trace-cc-006",
            )

        # Topic patterns should still be returned
        assert len(injected) == 1
        assert injected[0].pattern_text == "Topic pattern"
        assert cluster_ids_out == [cluster_id]
