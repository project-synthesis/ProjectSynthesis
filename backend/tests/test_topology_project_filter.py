"""Tests for Phase 2A + B6 topology endpoint project filtering."""

import numpy as np
import pytest

from app.models import PromptCluster


def _centroid() -> bytes:
    v = np.random.RandomState(42).randn(384).astype(np.float32)
    v = v / np.linalg.norm(v)
    return v.tobytes()


@pytest.mark.asyncio
async def test_health_endpoint_has_project_count(app_client):
    """Health endpoint includes project_count field."""
    client = app_client
    response = await client.get("/api/health?probes=false")
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


@pytest.mark.asyncio
async def test_b6_tree_filter_includes_in_project_clusters_and_domain_skeleton(
    app_client, db_session
):
    """B6: project_id filter returns:
    * the project node itself
    * all domain nodes (structural skeleton)
    * clusters whose dominant_project_id matches
    and excludes other projects' nodes.
    """
    # Seed: two project nodes, one shared domain node, and clusters across both.
    proj_a = PromptCluster(
        label="owner/repo-a", state="project", domain="general", task_type="general",
        member_count=0, centroid_embedding=_centroid(),
    )
    proj_b = PromptCluster(
        label="owner/repo-b", state="project", domain="general", task_type="general",
        member_count=0, centroid_embedding=_centroid(),
    )
    db_session.add_all([proj_a, proj_b])
    await db_session.flush()

    dom_coding = PromptCluster(
        label="coding", state="domain", domain="coding", task_type="coding",
        member_count=0, centroid_embedding=_centroid(),
    )
    db_session.add(dom_coding)
    await db_session.flush()

    # Active clusters: one in A, one in B, one un-attributed (Legacy-like)
    c_a = PromptCluster(
        label="c-in-a", state="active", domain="coding", task_type="coding",
        member_count=3, parent_id=dom_coding.id, centroid_embedding=_centroid(),
        dominant_project_id=proj_a.id,
    )
    c_b = PromptCluster(
        label="c-in-b", state="active", domain="coding", task_type="coding",
        member_count=3, parent_id=dom_coding.id, centroid_embedding=_centroid(),
        dominant_project_id=proj_b.id,
    )
    c_none = PromptCluster(
        label="c-unscoped", state="active", domain="coding", task_type="coding",
        member_count=1, parent_id=dom_coding.id, centroid_embedding=_centroid(),
        dominant_project_id=None,
    )
    db_session.add_all([c_a, c_b, c_none])
    await db_session.commit()

    # Request scoped view for project A
    resp = await app_client.get(f"/api/clusters/tree?project_id={proj_a.id}")
    assert resp.status_code == 200
    ids = {n["id"] for n in resp.json()["nodes"]}

    assert proj_a.id in ids, "requested project node must be included"
    assert proj_b.id not in ids, "other projects must be excluded"
    assert dom_coding.id in ids, "domain nodes are the structural skeleton"
    assert c_a.id in ids, "in-project cluster must be included"
    assert c_b.id not in ids, "cross-project cluster must be excluded"
    assert c_none.id not in ids, "un-attributed clusters are not in any project scope"

    # scope=all overrides the filter and returns everything
    resp_all = await app_client.get(
        f"/api/clusters/tree?project_id={proj_a.id}&scope=all"
    )
    assert resp_all.status_code == 200
    all_ids = {n["id"] for n in resp_all.json()["nodes"]}
    assert {proj_a.id, proj_b.id, dom_coding.id, c_a.id, c_b.id, c_none.id} <= all_ids


@pytest.mark.asyncio
async def test_b6_tree_dominant_project_id_field_present(app_client, db_session):
    """ClusterNode response exposes dominant_project_id for frontend scope logic."""
    proj = PromptCluster(
        label="owner/repo-x", state="project", domain="general", task_type="general",
        member_count=0, centroid_embedding=_centroid(),
    )
    db_session.add(proj)
    await db_session.flush()

    c = PromptCluster(
        label="c-x", state="active", domain="coding", task_type="coding",
        member_count=2, centroid_embedding=_centroid(),
        dominant_project_id=proj.id,
    )
    db_session.add(c)
    await db_session.commit()

    resp = await app_client.get("/api/clusters/tree")
    assert resp.status_code == 200
    node_map = {n["id"]: n for n in resp.json()["nodes"]}
    assert "dominant_project_id" in node_map[c.id]
    assert node_map[c.id]["dominant_project_id"] == proj.id
