from unittest.mock import AsyncMock, patch

import pytest

from app.models import PatternFamily


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
        """POST /api/patterns/match returns taxonomy-enriched match."""
        from app.services.taxonomy.engine import PatternMatch
        from unittest.mock import MagicMock, AsyncMock as _AsyncMock
        from app.main import app

        mock_family = MagicMock()
        mock_family.id = "fam1"
        mock_family.intent_label = "REST API patterns"
        mock_family.domain = "backend"
        mock_family.member_count = 5

        mock_result = PatternMatch(
            family=mock_family,
            taxonomy_node=None,
            meta_patterns=[],
            similarity=0.85,
            match_level="family",
        )

        mock_engine = MagicMock()
        mock_engine.match_prompt = _AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post("/api/patterns/match", json={"prompt_text": "this is a test prompt text"})
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match_level"] == "family"
        assert data["similarity"] == 0.85
        assert data["match"]["family"]["id"] == "fam1"

    @pytest.mark.asyncio
    async def test_match_endpoint_no_match(self, app_client):
        """POST /api/patterns/match returns empty on no match."""
        from unittest.mock import MagicMock, AsyncMock as _AsyncMock
        from app.main import app

        mock_engine = MagicMock()
        mock_engine.match_prompt = _AsyncMock(return_value=None)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post("/api/patterns/match", json={"prompt_text": "this is a test prompt text"})
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is None
        assert data["match_level"] == "none"

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
        assert resp.json() == {"id": "fam2", "intent_label": "new_label", "domain": "backend"}

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
    async def test_update_family_domain(self, app_client, db_session):
        """PATCH with domain only updates the domain."""
        family = PatternFamily(
            id="fam3", intent_label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch("/api/patterns/families/fam3", json={"domain": "frontend"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "frontend"
        assert data["intent_label"] == "test"  # unchanged

        await db_session.refresh(family)
        assert family.domain == "frontend"

    @pytest.mark.asyncio
    async def test_update_family_both_fields(self, app_client, db_session):
        """PATCH with both intent_label and domain updates both."""
        family = PatternFamily(
            id="fam4", intent_label="old", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch("/api/patterns/families/fam4", json={"intent_label": "new", "domain": "security"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_label"] == "new"
        assert data["domain"] == "security"

    @pytest.mark.asyncio
    async def test_update_family_freetext_domain(self, app_client, db_session):
        """PATCH with any string domain succeeds (free-text domains)."""
        family = PatternFamily(
            id="fam5", intent_label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch("/api/patterns/families/fam5", json={"domain": "web_api"})
        assert resp.status_code == 200
        assert resp.json()["domain"] == "web_api"

    @pytest.mark.asyncio
    async def test_update_family_empty_body_422(self, app_client, db_session):
        """PATCH with empty body (no fields) returns 422."""
        family = PatternFamily(
            id="fam6", intent_label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch("/api/patterns/families/fam6", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_stats(self, app_client):
        with patch("app.routers.patterns._graph_service.get_stats", new_callable=AsyncMock) as mock_stats:
            mock_stats.return_value = {"families": 5}
            resp = await app_client.get("/api/patterns/stats")
            assert resp.status_code == 200
            assert resp.json() == {"families": 5}


class TestDomainTypeValidation:
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
