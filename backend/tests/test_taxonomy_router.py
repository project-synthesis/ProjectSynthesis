"""Tests for /api/taxonomy/ endpoints (legacy — redirected to /api/clusters/).

These tests verify backward compatibility through 301/307 redirects.
The canonical endpoints are tested in test_clusters_router.py.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestTaxonomyEndpoints:
    @pytest.mark.asyncio
    async def test_get_tree_empty(self, app_client, db_session):
        """GET /api/taxonomy/tree redirects to /api/clusters/tree."""
        resp = await app_client.get("/api/taxonomy/tree", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []

    @pytest.mark.asyncio
    async def test_get_tree_min_persistence_param(self, app_client, db_session):
        """GET /api/taxonomy/tree preserves min_persistence through redirect."""
        resp = await app_client.get("/api/taxonomy/tree?min_persistence=0.5", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_get_stats(self, app_client, db_session):
        """GET /api/taxonomy/stats redirects to /api/clusters/stats."""
        resp = await app_client.get("/api/taxonomy/stats", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "q_system" in data
        assert "q_sparkline" in data
        assert isinstance(data["q_sparkline"], list)

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, app_client, db_session):
        """GET /api/taxonomy/node/{id} redirects and returns 404."""
        resp = await app_client.get("/api/taxonomy/node/nonexistent", follow_redirects=True)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_node_found(self, app_client, db_session):
        """GET /api/taxonomy/node/{id} redirects and returns node data."""
        import numpy as np

        from app.models import PromptCluster

        embedding = np.zeros(384, dtype=np.float32).tobytes()
        node = PromptCluster(
            id="node-1",
            label="Test Node",
            state="active",
            centroid_embedding=embedding,
            member_count=5,
            coherence=0.8,
            separation=0.9,
            persistence=0.7,
        )
        db_session.add(node)
        await db_session.commit()

        resp = await app_client.get("/api/taxonomy/node/node-1", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "node-1"
        assert data["label"] == "Test Node"
        assert "children" in data
        assert "breadcrumb" in data

    @pytest.mark.asyncio
    async def test_recluster_lock_held(self, app_client, db_session):
        """POST /api/taxonomy/recluster redirects and returns skipped."""
        mock_engine = AsyncMock()
        mock_engine.run_cold_path.return_value = None

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/taxonomy/recluster", follow_redirects=True)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_recluster_completed(self, app_client, db_session):
        """POST /api/taxonomy/recluster redirects and returns result."""
        from dataclasses import dataclass

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
            resp = await app_client.post("/api/taxonomy/recluster", follow_redirects=True)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["snapshot_id"] == "snap-1"
        assert data["q_system"] == 0.85
        assert data["nodes_created"] == 3

    @pytest.mark.asyncio
    async def test_recluster_error_returns_500(self, app_client, db_session):
        """POST /api/taxonomy/recluster returns 500 when engine raises (via redirect)."""
        mock_engine = AsyncMock()
        mock_engine.run_cold_path.side_effect = RuntimeError("HDBSCAN failed")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/taxonomy/recluster", follow_redirects=True)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_tree_db_error_returns_500(self, app_client, db_session):
        """GET /api/taxonomy/tree returns 500 when engine raises (via redirect)."""
        mock_engine = AsyncMock()
        mock_engine.get_tree.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/taxonomy/tree", follow_redirects=True)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_node_db_error_returns_500(self, app_client, db_session):
        """GET /api/taxonomy/node/{id} returns 500 when engine raises (via redirect)."""
        mock_engine = AsyncMock()
        mock_engine.get_node.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/taxonomy/node/test-id", follow_redirects=True)

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_get_stats_db_error_returns_500(self, app_client, db_session):
        """GET /api/taxonomy/stats returns 500 when engine raises (via redirect)."""
        mock_engine = AsyncMock()
        mock_engine.get_stats.side_effect = RuntimeError("DB connection lost")

        with patch("app.routers.clusters._get_engine", return_value=mock_engine):
            resp = await app_client.get("/api/taxonomy/stats", follow_redirects=True)

        assert resp.status_code == 500
