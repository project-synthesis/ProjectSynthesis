"""Tests for KnowledgeGraphService — graph building, edge computation, search."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.knowledge_graph import EDGE_THRESHOLD, KnowledgeGraphService


@pytest.fixture
def graph_service():
    embedding_svc = MagicMock()
    embedding_svc.aembed_single = AsyncMock(return_value=np.ones(384, dtype=np.float32))
    return KnowledgeGraphService(embedding_service=embedding_svc)


class TestGraphBuilding:
    @pytest.mark.asyncio
    async def test_empty_graph_returns_zero_counts(self, graph_service):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        graph = await graph_service.get_graph(mock_db)
        assert graph["center"]["total_families"] == 0
        assert graph["families"] == []
        assert graph["edges"] == []


class TestEdgeComputation:
    def test_edge_threshold_value(self):
        assert EDGE_THRESHOLD == 0.55

    def test_similar_centroids_produce_edge(self, graph_service):
        """Two families with similar centroids should produce an edge."""
        from app.models import PatternFamily

        vec_a = np.ones(384, dtype=np.float32)
        vec_b = np.ones(384, dtype=np.float32) * 0.95 + np.random.RandomState(42).randn(384).astype(np.float32) * 0.05

        families = [
            PatternFamily(id="a", intent_label="test-a", domain="backend", task_type="coding",
                         centroid_embedding=vec_a.tobytes(), usage_count=1, member_count=1, avg_score=7.0),
            PatternFamily(id="b", intent_label="test-b", domain="backend", task_type="coding",
                         centroid_embedding=vec_b.tobytes(), usage_count=1, member_count=1, avg_score=7.0),
        ]
        edges = graph_service._compute_edges(families)
        # Very similar vectors (0.95 base + 0.05 noise) should produce an edge
        assert len(edges) == 1
        assert edges[0]["from"] == "a"
        assert edges[0]["to"] == "b"
        assert edges[0]["weight"] >= EDGE_THRESHOLD


class TestSemanticSearch:
    @pytest.mark.asyncio
    async def test_search_returns_ranked_results(self, graph_service):
        from app.models import PatternFamily

        vec = np.ones(384, dtype=np.float32)
        families = [
            PatternFamily(id="a", intent_label="DI refactoring", domain="backend", task_type="coding",
                         centroid_embedding=vec.tobytes(), usage_count=3, member_count=3, avg_score=7.5),
        ]
        mock_fam_result = MagicMock()
        mock_fam_result.scalars.return_value.all.return_value = families
        mock_meta_result = MagicMock()
        mock_meta_result.scalars.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_fam_result, mock_meta_result])

        results = await graph_service.search_patterns(mock_db, "dependency injection", top_k=5)
        assert isinstance(results, list)
