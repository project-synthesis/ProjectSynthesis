"""Tests for the unified /api/clusters/ router."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import MetaPattern, PromptCluster


class TestClusterTree:
    @pytest.mark.asyncio
    async def test_get_cluster_tree_empty(self, app_client, db_session):
        """GET /api/clusters/tree returns domain nodes from seed + no others."""
        resp = await app_client.get("/api/clusters/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        # Only seed domain nodes should be present (no non-domain clusters)
        non_domain = [n for n in data["nodes"] if n.get("state") != "domain"]
        assert non_domain == []

    @pytest.mark.asyncio
    async def test_get_cluster_tree_with_data(self, app_client, db_session):
        """GET /api/clusters/tree returns nodes from DB."""
        cluster = PromptCluster(
            id="c1", label="Test", state="active", domain="backend",
            task_type="coding", member_count=3, persistence=0.8,
            centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.get("/api/clusters/tree")
        assert resp.status_code == 200
        data = resp.json()
        non_domain = [n for n in data["nodes"] if n.get("state") != "domain"]
        assert len(non_domain) == 1
        assert non_domain[0]["id"] == "c1"
        assert non_domain[0]["label"] == "Test"
        # Domain nodes should also be present in the tree (C2 fix)
        domain_nodes = [n for n in data["nodes"] if n.get("state") == "domain"]
        assert len(domain_nodes) == 8

    @pytest.mark.asyncio
    async def test_get_cluster_tree_min_persistence(self, app_client, db_session):
        """GET /api/clusters/tree respects min_persistence filter."""
        c1 = PromptCluster(
            id="c1", label="High", state="active", domain="backend",
            task_type="coding", persistence=0.9, centroid_embedding=b'\x00' * 384,
        )
        c2 = PromptCluster(
            id="c2", label="Low", state="active", domain="backend",
            task_type="coding", persistence=0.2, centroid_embedding=b'\x00' * 384,
        )
        db_session.add_all([c1, c2])
        await db_session.commit()

        resp = await app_client.get("/api/clusters/tree?min_persistence=0.5")
        assert resp.status_code == 200
        data = resp.json()
        non_domain = [n for n in data["nodes"] if n.get("state") != "domain"]
        assert len(non_domain) == 1
        assert non_domain[0]["id"] == "c1"


class TestClusterStats:
    @pytest.mark.asyncio
    async def test_get_cluster_stats(self, app_client, db_session):
        """GET /api/clusters/stats returns correct structure."""
        resp = await app_client.get("/api/clusters/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "q_system" in data
        assert "q_sparkline" in data
        assert "total_clusters" in data
        assert "q_trend" in data
        assert "q_current" in data
        assert "q_min" in data
        assert "q_max" in data
        assert "q_point_count" in data


class TestClusterDetail:
    @pytest.mark.asyncio
    async def test_get_cluster_not_found(self, app_client, db_session):
        """GET /api/clusters/{id} returns 404 for nonexistent cluster."""
        resp = await app_client.get("/api/clusters/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_cluster_found(self, app_client, db_session):
        """GET /api/clusters/{id} returns cluster detail with meta_patterns."""
        import numpy as np

        embedding = np.zeros(384, dtype=np.float32).tobytes()
        cluster = PromptCluster(
            id="c1", label="Test Cluster", state="active", domain="backend",
            task_type="coding", member_count=5, coherence=0.8, separation=0.9,
            persistence=0.7, centroid_embedding=embedding,
        )
        mp = MetaPattern(
            id="mp1", cluster_id="c1", pattern_text="Use REST conventions",
            source_count=3,
        )
        db_session.add_all([cluster, mp])
        await db_session.commit()

        resp = await app_client.get("/api/clusters/c1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "c1"
        assert data["label"] == "Test Cluster"
        assert "meta_patterns" in data
        assert len(data["meta_patterns"]) == 1
        assert data["meta_patterns"][0]["id"] == "mp1"
        assert "optimizations" in data
        assert "children" in data
        assert "breadcrumb" in data


class TestClusterUpdate:
    @pytest.mark.asyncio
    async def test_update_cluster_label(self, app_client, db_session):
        """PATCH /api/clusters/{id} updates label."""
        cluster = PromptCluster(
            id="c1", label="old", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c1", json={"intent_label": "new_label"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_label"] == "new_label"

        await db_session.refresh(cluster)
        assert cluster.label == "new_label"

    @pytest.mark.asyncio
    async def test_update_cluster_domain(self, app_client, db_session):
        """PATCH /api/clusters/{id} updates domain."""
        cluster = PromptCluster(
            id="c2", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c2", json={"domain": "frontend"})
        assert resp.status_code == 200
        assert resp.json()["domain"] == "frontend"

    @pytest.mark.asyncio
    async def test_update_cluster_domain_invalid_422(self, app_client, db_session):
        """PATCH /api/clusters/{id} returns 422 for an unknown domain."""
        cluster = PromptCluster(
            id="c2b", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c2b", json={"domain": "notadomain"})
        assert resp.status_code == 422
        assert "notadomain" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_cluster_state(self, app_client, db_session):
        """PATCH /api/clusters/{id} updates state."""
        cluster = PromptCluster(
            id="c3", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c3", json={"state": "template"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "template"

    @pytest.mark.asyncio
    async def test_update_cluster_not_found(self, app_client, db_session):
        """PATCH /api/clusters/{id} returns 404 for nonexistent cluster."""
        resp = await app_client.patch("/api/clusters/nonexistent", json={"intent_label": "x"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_cluster_empty_body_422(self, app_client, db_session):
        """PATCH /api/clusters/{id} returns 422 when no fields provided."""
        cluster = PromptCluster(
            id="c4", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c4", json={})
        assert resp.status_code == 422


class TestClusterMatch:
    @pytest.mark.asyncio
    async def test_match_cluster(self, app_client):
        """POST /api/clusters/match returns match result."""
        from app.main import app
        from app.services.taxonomy.engine import PatternMatch

        mock_family = MagicMock()
        mock_family.id = "c1"
        mock_family.label = "REST API patterns"
        mock_family.domain = "backend"
        mock_family.member_count = 5
        mock_family.color_hex = "#a855f7"

        mock_result = PatternMatch(
            cluster=mock_family, meta_patterns=[], similarity=0.85,
            match_level="family",
        )

        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "this is a test prompt text"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is not None

    @pytest.mark.asyncio
    async def test_match_cluster_no_match(self, app_client):
        """POST /api/clusters/match returns null match when nothing found."""
        from app.main import app

        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=None)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "this is a test prompt text"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is None


class TestClusterRecluster:
    @pytest.mark.asyncio
    async def test_recluster_lock_held(self, app_client, db_session):
        """POST /api/clusters/recluster returns skipped when lock held."""
        mock_engine = AsyncMock()
        mock_engine.run_cold_path.return_value = None

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/clusters/recluster")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_recluster_completed(self, app_client, db_session):
        """POST /api/clusters/recluster returns result on success."""

        @dataclass
        class FakeColdPathResult:
            snapshot_id: str = "snap-1"
            q_system: float = 0.85
            nodes_created: int = 3
            nodes_updated: int = 1
            umap_fitted: bool = True

        mock_engine = AsyncMock()
        mock_engine.run_cold_path.return_value = FakeColdPathResult()

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/clusters/recluster")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["snapshot_id"] == "snap-1"


class TestClusterTemplates:
    @pytest.mark.asyncio
    async def test_get_templates_empty(self, app_client, db_session):
        """GET /api/clusters/templates returns empty list when no templates."""
        resp = await app_client.get("/api/clusters/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []

    @pytest.mark.asyncio
    async def test_get_templates_filters_state(self, app_client, db_session):
        """GET /api/clusters/templates only returns state=template clusters."""
        c1 = PromptCluster(
            id="t1", label="Template", state="template", domain="backend",
            task_type="coding", avg_score=8.5, centroid_embedding=b'\x00' * 384,
        )
        c2 = PromptCluster(
            id="a1", label="Active", state="active", domain="backend",
            task_type="coding", avg_score=7.0, centroid_embedding=b'\x00' * 384,
        )
        db_session.add_all([c1, c2])
        await db_session.commit()

        resp = await app_client.get("/api/clusters/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "t1"


class TestLegacyRedirects:
    @pytest.mark.asyncio
    async def test_legacy_taxonomy_tree_redirect(self, app_client):
        """GET /api/taxonomy/tree redirects to /api/clusters/tree."""
        resp = await app_client.get("/api/taxonomy/tree", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/tree" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_taxonomy_stats_redirect(self, app_client):
        """GET /api/taxonomy/stats redirects to /api/clusters/stats."""
        resp = await app_client.get("/api/taxonomy/stats", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/stats" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_taxonomy_node_redirect(self, app_client):
        """GET /api/taxonomy/node/{id} redirects to /api/clusters/{id}."""
        resp = await app_client.get("/api/taxonomy/node/abc123", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/abc123" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_families_redirect(self, app_client):
        """GET /api/patterns/families redirects to /api/clusters."""
        resp = await app_client.get("/api/patterns/families", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_families_id_redirect(self, app_client):
        """GET /api/patterns/families/{id} redirects to /api/clusters/{id}."""
        resp = await app_client.get("/api/patterns/families/fam1", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/fam1" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_match_redirect(self, app_client):
        """POST /api/patterns/match redirects to /api/clusters/match."""
        resp = await app_client.post(
            "/api/patterns/match",
            json={"prompt_text": "test prompt text here"},
            follow_redirects=False,
        )
        assert resp.status_code == 307
        assert "/api/clusters/match" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_graph_redirect(self, app_client):
        """GET /api/patterns/graph redirects to /api/clusters/tree."""
        resp = await app_client.get("/api/patterns/graph", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/tree" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_stats_redirect(self, app_client):
        """GET /api/patterns/stats redirects to /api/clusters/stats."""
        resp = await app_client.get("/api/patterns/stats", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/stats" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_patterns_search_redirect(self, app_client):
        """GET /api/patterns/search redirects to /api/clusters/tree."""
        resp = await app_client.get("/api/patterns/search", follow_redirects=False)
        assert resp.status_code == 301
        assert "/api/clusters/tree" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_legacy_redirect_preserves_query_params(self, app_client):
        """Legacy redirects preserve query parameters."""
        resp = await app_client.get(
            "/api/taxonomy/tree?min_persistence=0.5",
            follow_redirects=False,
        )
        assert resp.status_code == 301
        location = resp.headers.get("location", "")
        assert "/api/clusters/tree" in location
        assert "min_persistence=0.5" in location


class TestSimilarityEdges:
    @pytest.mark.asyncio
    async def test_similarity_edges_empty(self, app_client, db_session):
        """GET /api/clusters/similarity-edges returns empty list when no clusters."""
        resp = await app_client.get("/api/clusters/similarity-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert "edges" in data
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_similarity_edges_with_similar_clusters(self, app_client, db_session):
        """GET /api/clusters/similarity-edges returns edges for similar clusters."""
        import numpy as np

        from app.services.taxonomy.embedding_index import EmbeddingIndex

        # Build a small index with near-identical embeddings
        idx = EmbeddingIndex(dim=384)
        emb = np.random.randn(384).astype(np.float32)
        emb /= np.linalg.norm(emb)
        await idx.upsert("c1", emb)
        await idx.upsert("c2", emb + np.random.randn(384).astype(np.float32) * 0.01)

        mock_engine = MagicMock()
        mock_engine.embedding_index = idx

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/clusters/similarity-edges?threshold=0.5&max_edges=10")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) == 1
        edge = data["edges"][0]
        assert "from_id" in edge
        assert "to_id" in edge
        assert "similarity" in edge
        assert edge["similarity"] > 0.9

    @pytest.mark.asyncio
    async def test_similarity_edges_query_params(self, app_client, db_session):
        """GET /api/clusters/similarity-edges validates query params."""
        # Threshold out of range
        resp = await app_client.get("/api/clusters/similarity-edges?threshold=1.5")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_similarity_edges_error_500(self, app_client, db_session):
        """GET /api/clusters/similarity-edges returns 500 on engine error."""
        mock_engine = MagicMock()
        mock_engine.embedding_index.pairwise_similarities.side_effect = RuntimeError("boom")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/clusters/similarity-edges")

        assert resp.status_code == 500


class TestInjectionEdges:
    @pytest.mark.asyncio
    async def test_injection_edges_empty(self, app_client, db_session):
        """GET /api/clusters/injection-edges returns empty list when no data."""
        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert "edges" in data
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_injection_edges_with_data(self, app_client, db_session):
        """GET /api/clusters/injection-edges returns aggregated directed edges."""
        from app.models import Optimization, OptimizationPattern

        # Create source and target clusters
        source = PromptCluster(
            id="src-1", label="Source Cluster", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        target = PromptCluster(
            id="tgt-1", label="Target Cluster", state="active", domain="frontend",
            task_type="writing", centroid_embedding=b'\x00' * 384,
        )
        db_session.add_all([source, target])

        # Create two optimizations assigned to the target cluster
        opt1 = Optimization(
            id="opt-1", raw_prompt="test prompt 1", cluster_id="tgt-1",
            status="completed",
        )
        opt2 = Optimization(
            id="opt-2", raw_prompt="test prompt 2", cluster_id="tgt-1",
            status="completed",
        )
        db_session.add_all([opt1, opt2])

        # Create injection provenance records
        db_session.add(OptimizationPattern(
            optimization_id="opt-1", cluster_id="src-1",
            relationship="injected", similarity=0.78,
        ))
        db_session.add(OptimizationPattern(
            optimization_id="opt-2", cluster_id="src-1",
            relationship="injected", similarity=0.82,
        ))
        await db_session.commit()

        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) == 1
        edge = data["edges"][0]
        assert edge["source_id"] == "src-1"
        assert edge["target_id"] == "tgt-1"
        assert edge["weight"] == 2

    @pytest.mark.asyncio
    async def test_injection_edges_excludes_archived_clusters(self, app_client, db_session):
        """GET /api/clusters/injection-edges excludes edges involving archived clusters."""
        from app.models import Optimization, OptimizationPattern

        active = PromptCluster(
            id="active-1", label="Active", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        archived = PromptCluster(
            id="archived-1", label="Archived", state="archived", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add_all([active, archived])

        opt = Optimization(
            id="opt-arc", raw_prompt="test", cluster_id="active-1",
            status="completed",
        )
        db_session.add(opt)
        db_session.add(OptimizationPattern(
            optimization_id="opt-arc", cluster_id="archived-1",
            relationship="injected",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        # Archived source cluster should be excluded
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_injection_edges_excludes_self_loops(self, app_client, db_session):
        """GET /api/clusters/injection-edges excludes self-referencing edges."""
        from app.models import Optimization, OptimizationPattern

        cluster = PromptCluster(
            id="self-1", label="Self", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)

        opt = Optimization(
            id="opt-self", raw_prompt="test", cluster_id="self-1",
            status="completed",
        )
        db_session.add(opt)
        db_session.add(OptimizationPattern(
            optimization_id="opt-self", cluster_id="self-1",
            relationship="injected",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_injection_edges_ignores_non_injected_relationships(self, app_client, db_session):
        """GET /api/clusters/injection-edges only includes relationship='injected'."""
        from app.models import Optimization, OptimizationPattern

        source = PromptCluster(
            id="src-2", label="Source", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        target = PromptCluster(
            id="tgt-2", label="Target", state="active", domain="frontend",
            task_type="writing", centroid_embedding=b'\x00' * 384,
        )
        db_session.add_all([source, target])

        opt = Optimization(
            id="opt-applied", raw_prompt="test", cluster_id="tgt-2",
            status="completed",
        )
        db_session.add(opt)
        # "applied" relationship should NOT appear in injection edges
        db_session.add(OptimizationPattern(
            optimization_id="opt-applied", cluster_id="src-2",
            relationship="applied",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []

    @pytest.mark.asyncio
    async def test_injection_edges_excludes_null_cluster_optimizations(self, app_client, db_session):
        """GET /api/clusters/injection-edges excludes optimizations with no cluster_id."""
        from app.models import Optimization, OptimizationPattern

        source = PromptCluster(
            id="src-3", label="Source", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(source)

        # Optimization with no cluster_id (not yet assigned)
        opt = Optimization(
            id="opt-null", raw_prompt="test", cluster_id=None,
            status="completed",
        )
        db_session.add(opt)
        db_session.add(OptimizationPattern(
            optimization_id="opt-null", cluster_id="src-3",
            relationship="injected",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/clusters/injection-edges")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []


class TestClusterErrorHandling:
    """Error handling tests for cluster endpoints (via both direct and legacy paths)."""

    @pytest.mark.asyncio
    async def test_recluster_error_returns_500(self, app_client, db_session):
        """POST /api/clusters/recluster returns 500 when engine raises."""
        from unittest.mock import AsyncMock, patch

        mock_engine = AsyncMock()
        mock_engine.run_cold_path.side_effect = RuntimeError("HDBSCAN failed")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/clusters/recluster")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_tree_db_error_returns_500(self, app_client, db_session):
        """GET /api/clusters/tree returns 500 when engine raises."""
        from unittest.mock import AsyncMock, patch

        mock_engine = AsyncMock()
        mock_engine.get_tree.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/clusters/tree")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_node_db_error_returns_500(self, app_client, db_session):
        """GET /api/clusters/{id} returns 500 when engine raises."""
        from unittest.mock import AsyncMock, patch

        mock_engine = AsyncMock()
        mock_engine.get_node.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/clusters/test-id")

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_stats_db_error_returns_500(self, app_client, db_session):
        """GET /api/clusters/stats returns 500 when engine raises."""
        from unittest.mock import AsyncMock, patch

        mock_engine = AsyncMock()
        mock_engine.get_stats.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/clusters/stats")

        assert resp.status_code == 500


class TestDomainFieldValidation:
    """Domain field validation for AnalysisResult schema."""

    def test_valid_domain_accepted(self):
        from app.schemas.pipeline_contracts import AnalysisResult
        result = AnalysisResult(
            task_type="coding", weaknesses=[], strengths=[],
            selected_strategy="auto", strategy_rationale="test",
            confidence=0.9, domain="backend",
        )
        assert result.domain == "backend"

    def test_freetext_domain_accepted(self):
        """Free-text domains like 'web' are now valid strings."""
        from app.schemas.pipeline_contracts import AnalysisResult
        result = AnalysisResult(
            task_type="coding", weaknesses=[], strengths=[],
            selected_strategy="auto", strategy_rationale="test",
            confidence=0.9, domain="web",
        )
        assert result.domain == "web"

    def test_missing_domain_defaults_to_general(self):
        from app.schemas.pipeline_contracts import AnalysisResult
        result = AnalysisResult(
            task_type="coding", weaknesses=[], strengths=[],
            selected_strategy="auto", strategy_rationale="test",
            confidence=0.9,
        )
        assert result.domain == "general"
