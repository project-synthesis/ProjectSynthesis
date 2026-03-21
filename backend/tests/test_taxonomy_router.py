"""Tests for /api/taxonomy/ endpoints."""

from unittest.mock import AsyncMock, patch

import pytest


class TestTaxonomyEndpoints:
    @pytest.mark.asyncio
    async def test_get_tree_empty(self, app_client, db_session):
        """GET /api/taxonomy/tree returns empty list on fresh DB."""
        resp = await app_client.get("/api/taxonomy/tree")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []

    @pytest.mark.asyncio
    async def test_get_tree_min_persistence_param(self, app_client, db_session):
        """GET /api/taxonomy/tree accepts min_persistence query param."""
        resp = await app_client.get("/api/taxonomy/tree?min_persistence=0.5")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_get_stats(self, app_client, db_session):
        """GET /api/taxonomy/stats returns correct structure."""
        resp = await app_client.get("/api/taxonomy/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "confirmed_nodes" in data
        assert "candidate_nodes" in data
        assert "total_families" in data

    @pytest.mark.asyncio
    async def test_get_node_not_found(self, app_client, db_session):
        """GET /api/taxonomy/node/{id} returns 404 for nonexistent node."""
        resp = await app_client.get("/api/taxonomy/node/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_node_found(self, app_client, db_session):
        """GET /api/taxonomy/node/{id} returns node data when it exists."""
        import numpy as np

        from app.models import TaxonomyNode

        embedding = np.zeros(384, dtype=np.float32).tobytes()
        node = TaxonomyNode(
            id="node-1",
            label="Test Node",
            state="confirmed",
            centroid_embedding=embedding,
            member_count=5,
            coherence=0.8,
            separation=0.9,
            persistence=0.7,
        )
        db_session.add(node)
        await db_session.commit()

        resp = await app_client.get("/api/taxonomy/node/node-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "node-1"
        assert data["label"] == "Test Node"
        assert "children" in data
        assert "breadcrumb" in data

    @pytest.mark.asyncio
    async def test_recluster_lock_held(self, app_client, db_session):
        """POST /api/taxonomy/recluster returns skipped when lock held."""

        mock_engine = AsyncMock()
        mock_engine.run_cold_path.return_value = None

        with patch("app.routers.taxonomy._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/taxonomy/recluster")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_recluster_completed(self, app_client, db_session):
        """POST /api/taxonomy/recluster returns result on success."""
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

        with patch("app.routers.taxonomy._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/taxonomy/recluster")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["snapshot_id"] == "snap-1"
        assert data["q_system"] == 0.85
        assert data["nodes_created"] == 3

    @pytest.mark.asyncio
    async def test_recluster_error_returns_500(self, app_client, db_session):
        """POST /api/taxonomy/recluster returns 500 when engine raises."""

        mock_engine = AsyncMock()
        mock_engine.run_cold_path.side_effect = RuntimeError("HDBSCAN failed")

        with patch("app.routers.taxonomy._get_engine", return_value=mock_engine):
            resp = await app_client.post("/api/taxonomy/recluster")

        assert resp.status_code == 500
