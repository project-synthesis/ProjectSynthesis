"""Tests for /api/patterns/ endpoints (legacy — redirected to /api/clusters/).

These tests verify backward compatibility through 301/307 redirects.
The canonical endpoints are tested in test_clusters_router.py.
"""

import pytest

from app.models import PromptCluster


class TestPatternsEndpoints:
    @pytest.mark.asyncio
    async def test_match_endpoint(self, app_client):
        """POST /api/patterns/match redirects to /api/clusters/match."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock

        from app.main import app
        from app.services.taxonomy.engine import PatternMatch

        mock_family = MagicMock()
        mock_family.id = "fam1"
        mock_family.label = "REST API patterns"
        mock_family.domain = "backend"
        mock_family.member_count = 5
        mock_family.color_hex = "#a855f7"

        mock_result = PatternMatch(
            cluster=mock_family,
            meta_patterns=[],
            similarity=0.85,
            match_level="family",
        )

        mock_engine = MagicMock()
        mock_engine.match_prompt = _AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            # Legacy endpoint now redirects — verify redirect, then follow
            resp = await app_client.post(
                "/api/patterns/match",
                json={"prompt_text": "this is a test prompt text"},
                follow_redirects=True,
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is not None
        assert data["match"]["cluster"]["id"] == "fam1"

    @pytest.mark.asyncio
    async def test_match_endpoint_no_match(self, app_client):
        """POST /api/patterns/match returns empty on no match (via redirect)."""
        from unittest.mock import AsyncMock as _AsyncMock
        from unittest.mock import MagicMock

        from app.main import app

        mock_engine = MagicMock()
        mock_engine.match_prompt = _AsyncMock(return_value=None)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/patterns/match",
                json={"prompt_text": "this is a test prompt text"},
                follow_redirects=True,
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is None

    @pytest.mark.asyncio
    async def test_families_endpoint(self, app_client, db_session):
        """GET /api/patterns/families redirects to /api/clusters (via redirect)."""
        family = PromptCluster(
            id="fam1", label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        # Legacy redirect — verify it redirects with 301
        resp_redir = await app_client.get("/api/patterns/families?domain=backend", follow_redirects=False)
        assert resp_redir.status_code == 301

    @pytest.mark.asyncio
    async def test_get_family(self, app_client, db_session):
        """GET /api/patterns/families/{id} redirects to /api/clusters/{id}."""
        family = PromptCluster(
            id="fam1", label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.get("/api/patterns/families/fam1", follow_redirects=True)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "fam1"
        assert data["label"] == "test"
        assert "meta_patterns" in data
        assert "optimizations" in data

    @pytest.mark.asyncio
    async def test_rename_family(self, app_client, db_session):
        """PATCH /api/patterns/families/{id} redirects and updates."""
        family = PromptCluster(
            id="fam2", label="old", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch(
            "/api/patterns/families/fam2",
            json={"intent_label": "new_label"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_label"] == "new_label"

        await db_session.refresh(family)
        assert family.label == "new_label"

    @pytest.mark.asyncio
    async def test_update_family_domain(self, app_client, db_session):
        """PATCH with domain only updates the domain (via redirect)."""
        family = PromptCluster(
            id="fam3", label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch(
            "/api/patterns/families/fam3",
            json={"domain": "frontend"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "frontend"
        assert data["intent_label"] == "test"

        await db_session.refresh(family)
        assert family.domain == "frontend"

    @pytest.mark.asyncio
    async def test_update_family_both_fields(self, app_client, db_session):
        """PATCH with both intent_label and domain updates both (via redirect)."""
        family = PromptCluster(
            id="fam4", label="old", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch(
            "/api/patterns/families/fam4",
            json={"intent_label": "new", "domain": "security"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_label"] == "new"
        assert data["domain"] == "security"

    @pytest.mark.asyncio
    async def test_update_family_freetext_domain(self, app_client, db_session):
        """PATCH with any string domain succeeds (via redirect)."""
        family = PromptCluster(
            id="fam5", label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch(
            "/api/patterns/families/fam5",
            json={"domain": "web_api"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert resp.json()["domain"] == "web_api"

    @pytest.mark.asyncio
    async def test_update_family_empty_body_422(self, app_client, db_session):
        """PATCH with empty body returns 422 (via redirect)."""
        family = PromptCluster(
            id="fam6", label="test", domain="backend", task_type="coding",
            usage_count=5, member_count=2, avg_score=8.0, centroid_embedding=b'\x00' * 384
        )
        db_session.add(family)
        await db_session.commit()

        resp = await app_client.patch(
            "/api/patterns/families/fam6",
            json={},
            follow_redirects=True,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_family_not_found(self, app_client):
        """GET /api/patterns/families/{id} returns 404 via redirect."""
        resp = await app_client.get(
            "/api/patterns/families/nonexistent",
            follow_redirects=True,
        )
        assert resp.status_code == 404


class TestDomainFieldValidation:
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
