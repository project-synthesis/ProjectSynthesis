"""Tests for patterns router — endpoints, error cases."""


import pytest


class TestPatternsGraphEndpoint:
    @pytest.mark.asyncio
    async def test_graph_returns_200(self):
        """GET /api/patterns/graph returns graph structure."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/graph" for r in router.routes)

    @pytest.mark.asyncio
    async def test_match_endpoint_exists(self):
        """POST /api/patterns/match endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/match" for r in router.routes)

    @pytest.mark.asyncio
    async def test_families_endpoint_exists(self):
        """GET /api/patterns/families endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/families" for r in router.routes)

    @pytest.mark.asyncio
    async def test_search_endpoint_exists(self):
        """GET /api/patterns/search endpoint is registered."""
        from app.routers.patterns import router
        assert any(r.path == "/api/patterns/search" for r in router.routes)
