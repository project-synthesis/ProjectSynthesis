import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.models import PatternFamily
from datetime import datetime

class TestPatternsEndpoints:
    @pytest.mark.asyncio
    async def test_graph_returns_200(self, app_client):
        with patch("app.routers.patterns._graph_service.get_graph", new_callable=AsyncMock) as mock_graph:
            mock_graph.return_value = {"nodes": [], "edges": [], "domains": {}}
            resp = await app_client.get("/api/patterns/graph")
            assert resp.status_code == 200
            assert resp.json() == {"nodes": [], "edges": [], "domains": {}}

    @pytest.mark.asyncio
    async def test_match_endpoint(self, app_client):
        with patch("app.routers.patterns._matcher_service.match", new_callable=AsyncMock) as mock_match:
            mock_match.return_value = {"family_id": "fam1", "score": 0.85}
            resp = await app_client.post("/api/patterns/match", json={"prompt_text": "this is a test prompt text"})
            assert resp.status_code == 200
            assert resp.json() == {"match": {"family_id": "fam1", "score": 0.85}}
            
    @pytest.mark.asyncio
    async def test_match_endpoint_no_match(self, app_client):
        with patch("app.routers.patterns._matcher_service.match", new_callable=AsyncMock) as mock_match:
            mock_match.return_value = None
            resp = await app_client.post("/api/patterns/match", json={"prompt_text": "this is a test prompt text"})
            assert resp.status_code == 200
            assert resp.json() == {"match": None}

    @pytest.mark.asyncio
    async def test_families_endpoint(self, app_client, db_session):
        # Insert a family to db
        family = PatternFamily(
            id="fam1", intent_label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.get("/api/patterns/families?domain=backend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["id"] == "fam1"

    @pytest.mark.asyncio
    async def test_get_family(self, app_client):
        with patch("app.routers.patterns._graph_service.get_family_detail", new_callable=AsyncMock) as mock_detail:
            mock_detail.return_value = {"id": "fam1"}
            resp = await app_client.get("/api/patterns/families/fam1")
            assert resp.status_code == 200
            assert resp.json() == {"id": "fam1"}

    @pytest.mark.asyncio
    async def test_rename_family(self, app_client, db_session):
        # Insert a family to db
        family = PatternFamily(
            id="fam2", intent_label="old", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch("/api/patterns/families/fam2", json={"intent_label": "new_label"})
        assert resp.status_code == 200
        assert resp.json() == {"id": "fam2", "intent_label": "new_label"}

        # Validate db is updated
        await db_session.refresh(family)
        assert family.intent_label == "new_label"

    @pytest.mark.asyncio
    async def test_search_patterns(self, app_client):
        with patch("app.routers.patterns._graph_service.search_patterns", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"results": []}
            resp = await app_client.get("/api/patterns/search?q=test")
            assert resp.status_code == 200
            assert resp.json() == {"results": []}
            
    @pytest.mark.asyncio
    async def test_get_stats(self, app_client):
        with patch("app.routers.patterns._graph_service.get_stats", new_callable=AsyncMock) as mock_stats:
            mock_stats.return_value = {"families": 5}
            resp = await app_client.get("/api/patterns/stats")
            assert resp.status_code == 200
            assert resp.json() == {"families": 5}
