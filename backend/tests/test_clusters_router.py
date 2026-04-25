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
    async def test_update_cluster_domain_not_accepted(self, app_client, db_session):
        """PATCH /api/clusters/{id} does not accept domain changes.

        Domain reassignment is disallowed — it causes cluster fragmentation
        via cross-domain merge prevention, wrong warm-path merges, and
        corrupt tree topology. Domain is set automatically by the taxonomy
        engine from optimization classification.
        """
        cluster = PromptCluster(
            id="c2", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
        )
        db_session.add(cluster)
        await db_session.commit()

        # domain-only payload rejected (no valid field provided)
        resp = await app_client.patch("/api/clusters/c2", json={"domain": "frontend"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_update_cluster_state_template_returns_400(self, app_client, db_session):
        """PATCH /api/clusters/{id} with state=template is rejected (use fork-template).

        Task 25: "template" removed from ClusterUpdateRequest.state Literal.
        Pydantic rejects at the schema boundary (422) before the router guard
        (400 + 'fork-template' hint) can run. Both codes indicate rejection.
        """
        cluster = PromptCluster(
            id="c3-promote-ok", label="test", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
            member_count=5, avg_score=7.5, usage_count=2,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c3-promote-ok", json={"state": "template"})
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for state='template'; got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_template_promotion_blocked_low_score(self, app_client, db_session):
        """PATCH /api/clusters/{id} with state=template is rejected regardless of score.

        Task 25: Pydantic Literal narrowing means 422 is now the expected code
        (schema rejection before the 400 quality-gate handler runs).
        """
        cluster = PromptCluster(
            id="c4-low-score", label="low-score", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
            member_count=5, avg_score=4.0, usage_count=2,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c4-low-score", json={"state": "template"})
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for state='template'; got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_template_promotion_blocked_no_members(self, app_client, db_session):
        """PATCH /api/clusters/{id} with state=template is rejected regardless of members.

        Task 25: Pydantic Literal narrowing means 422 is now the expected code
        (schema rejection before the 400 quality-gate handler runs).
        """
        cluster = PromptCluster(
            id="c5-no-members", label="empty", state="active", domain="backend",
            task_type="coding", centroid_embedding=b'\x00' * 384,
            member_count=1, avg_score=8.0, usage_count=0,
        )
        db_session.add(cluster)
        await db_session.commit()

        resp = await app_client.patch("/api/clusters/c5-no-members", json={"state": "template"})
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for state='template'; got {resp.status_code}"
        )

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

    @pytest.mark.asyncio
    async def test_match_response_includes_match_level(self, app_client):
        """B2+B3: match response exposes match_level ∈ {'family', 'cluster'}.

        Mocks engine.match_prompt() — the test fixture has no embedding
        service (conftest.py:114 sets embedding_service=None), so a real
        similarity match against a seeded cluster isn't achievable.
        """
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "API endpoint patterns"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 5
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#a855f7"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.85,
            match_level="cluster",
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
        body = resp.json()
        assert body["match"] is not None
        assert "match_level" in body["match"], "match_level key missing from response"
        assert body["match"]["match_level"] in {"family", "cluster"}

    @pytest.mark.asyncio
    async def test_match_response_includes_cross_cluster_patterns(self, app_client):
        """B1: cross_cluster_patterns surfaces from PatternMatch, disjoint from meta_patterns."""
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "JWT validation patterns"
        mock_cluster.domain = "security"
        mock_cluster.member_count = 4
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#ff2255"

        # Meta-pattern on the target cluster.
        meta_mp = MagicMock()
        meta_mp.id = "mp-local"
        meta_mp.pattern_text = "Validate token signature algorithm"
        meta_mp.source_count = 3

        # Cross-cluster (globally-promoted) patterns from sibling clusters.
        gp1 = MagicMock()
        gp1.id = "gp-1"
        gp1.pattern_text = "Universal A"
        gp1.source_count = 5
        gp2 = MagicMock()
        gp2.id = "gp-2"
        gp2.pattern_text = "Universal B"
        gp2.source_count = 4

        mock_result = PatternMatch(
            cluster=mock_cluster,
            meta_patterns=[meta_mp],
            similarity=0.88,
            match_level="cluster",
            cross_cluster_patterns=[gp1, gp2],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "validate jwt for incoming api requests"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        body = resp.json()
        assert "cross_cluster_patterns" in body["match"], "key missing"
        assert len(body["match"]["cross_cluster_patterns"]) == 2

        # Disjointness guarantee (engine already enforces this per matching.py:404-457,
        # but the router must not accidentally merge them).
        meta_ids = {p["id"] for p in body["match"]["meta_patterns"]}
        cross_ids = {p["id"] for p in body["match"]["cross_cluster_patterns"]}
        assert meta_ids.isdisjoint(cross_ids)

    @pytest.mark.asyncio
    async def test_match_response_empty_cross_cluster_patterns(self, app_client):
        """B5: cross_cluster_patterns is always present; [] when engine returns no globals."""
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "Solo cluster"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 2
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#b44aff"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.7,
            match_level="cluster", cross_cluster_patterns=[],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "build a backend service"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        assert resp.json()["match"]["cross_cluster_patterns"] == []

    @pytest.mark.asyncio
    async def test_match_response_preserves_existing_fields(self, app_client):
        """B4: additive delta must not remove or rename any pre-existing field.

        Locks the contract so a future refactor of match_dict assembly can't
        silently break consumers.
        """
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "Test cluster"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 3
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#b44aff"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.75,
            match_level="cluster", cross_cluster_patterns=[],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "write a function that validates email"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        match = resp.json()["match"]
        assert match is not None

        # Pre-existing top-level keys must still be present.
        assert "cluster" in match
        assert "meta_patterns" in match
        assert "similarity" in match

        # Pre-existing cluster sub-keys.
        cl = match["cluster"]
        for key in ("id", "label", "domain", "member_count"):
            assert key in cl, f"missing pre-existing key: {key}"


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
            q_before: float | None = 0.80
            q_after: float | None = 0.85
            accepted: bool = True
            nodes_created: int = 3
            nodes_updated: int = 1
            umap_fitted: bool = True
            q_system: float | None = None  # backward compat — auto-set from q_after

            def __post_init__(self) -> None:
                if self.q_system is None and self.q_after is not None:
                    self.q_system = self.q_after

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
    async def test_get_templates_returns_410(self, app_client, db_session):
        """GET /api/clusters/templates returns 410 Gone — use GET /api/templates."""
        resp = await app_client.get("/api/clusters/templates")
        assert resp.status_code == 410
        detail = resp.json()["detail"]
        if isinstance(detail, dict):
            detail = detail.get("detail", "")
        assert "GET /api/templates" in detail

    @pytest.mark.asyncio
    async def test_get_templates_410_regardless_of_data(self, app_client, db_session):
        """GET /api/clusters/templates always returns 410 regardless of DB state."""
        c1 = PromptCluster(
            id="t1", label="Template", state="template", domain="backend",
            task_type="coding", avg_score=8.5, centroid_embedding=b'\x00' * 384,
        )
        db_session.add(c1)
        await db_session.commit()

        resp = await app_client.get("/api/clusters/templates")
        assert resp.status_code == 410


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


class TestClusterActivityHistory:
    """GET /api/clusters/activity/history — date-only and since/until range variant."""

    @pytest.mark.asyncio
    async def test_activity_history_range_multi_day(self, app_client):
        """AH1: ?since=X&until=Y fans out over the JSONL files in the range."""
        from unittest.mock import patch

        mock_logger = MagicMock()
        def _get_history(date, limit, offset):
            if date == "2026-04-23":
                return [{"ts": "2026-04-23T10:00Z", "path": "warm", "op": "discover", "decision": "domains_created"}]
            if date == "2026-04-24":
                return [{"ts": "2026-04-24T09:00Z", "path": "hot", "op": "match", "decision": "matched"}]
            return []
        mock_logger.get_history = _get_history

        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": "2026-04-23", "until": "2026-04-24"},
            )
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        assert events[0]["ts"] == "2026-04-24T09:00Z"  # reverse chrono

    @pytest.mark.asyncio
    async def test_activity_history_range_missing_days(self, app_client):
        """AH2: missing JSONL files in the range are skipped, not errored."""
        from unittest.mock import patch
        mock_logger = MagicMock()
        mock_logger.get_history = lambda date, limit, offset: (
            [{"ts": f"{date}T10:00Z", "path": "warm", "op": "discover", "decision": "d"}]
            if date in {"2026-04-22", "2026-04-24"} else []
        )
        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": "2026-04-22", "until": "2026-04-24"},
            )
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 2  # only 2 days had data

    @pytest.mark.asyncio
    async def test_activity_history_range_mutex_with_date(self, app_client):
        """AH3: date + since/until together is 422."""
        resp = await app_client.get(
            "/api/clusters/activity/history",
            params={"date": "2026-04-24", "since": "2026-04-22", "until": "2026-04-24"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_activity_history_range_oversized(self, app_client):
        """AH4: range > 30 days is 422."""
        resp = await app_client.get(
            "/api/clusters/activity/history",
            params={"since": "2026-03-01", "until": "2026-04-15"},
        )
        assert resp.status_code == 422
        # Must reject because the range exceeds 30 days — not because `date`
        # is missing. Today the endpoint only knows `date`, so it 422s with
        # `Field required` on `date`; once `since`/`until` is implemented the
        # validation must explicitly mention the range size.
        body = resp.json()
        rendered = repr(body).lower()
        assert "date" not in rendered or "30" in rendered, (
            f"422 must come from range-size validation, not missing-date: {body}"
        )

    @pytest.mark.asyncio
    async def test_activity_history_range_since_only(self, app_client):
        """AH5: since alone defaults until=today UTC."""
        from unittest.mock import patch
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        mock_logger = MagicMock()
        dates_called: list[str] = []
        def _get_history(date, limit, offset):
            dates_called.append(date)
            return []
        mock_logger.get_history = _get_history

        with patch("app.routers.clusters.get_event_logger", return_value=mock_logger):
            resp = await app_client.get(
                "/api/clusters/activity/history",
                params={"since": today},  # `until` omitted
            )
        assert resp.status_code == 200
        assert today in dates_called
