"""Tests for Phase 2A topology endpoint project filtering."""

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_has_project_count(app_client):
    """Health endpoint includes project_count field."""
    client = app_client
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert "project_count" in data
    assert isinstance(data["project_count"], int)


@pytest.mark.asyncio
async def test_tree_endpoint_accepts_project_id(app_client):
    """Tree endpoint accepts optional project_id query param."""
    client = app_client
    # Without filter — should work
    response = await client.get("/api/clusters/tree")
    assert response.status_code == 200

    # With filter — should work (even if no matching project)
    response = await client.get("/api/clusters/tree?project_id=nonexistent")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cluster_detail_has_member_counts_by_project():
    """ClusterDetail schema has member_counts_by_project field."""
    from app.schemas.clusters import ClusterDetail

    assert hasattr(ClusterDetail, "model_fields")
    fields = ClusterDetail.model_fields
    assert "member_counts_by_project" in fields
