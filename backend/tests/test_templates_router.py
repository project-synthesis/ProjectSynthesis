"""Tests for /api/templates router."""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded_mature_cluster(db_session):
    """A mature cluster with one high-scoring optimization, ready to fork."""
    from app.models import Optimization, PromptCluster
    cid = f"c_{uuid.uuid4().hex[:8]}"
    db_session.add(PromptCluster(
        id=cid, label="test_cluster", state="mature",
        member_count=5, coherence=0.8, avg_score=7.8, usage_count=3,
    ))
    db_session.add(Optimization(
        id=uuid.uuid4().hex, cluster_id=cid,
        raw_prompt="raw", optimized_prompt="optimized",
        strategy_used="auto", overall_score=7.8,
    ))
    await db_session.flush()  # NOT commit — preserves rollback isolation
    return cid


@pytest_asyncio.fixture
async def seeded_template_id(db_session, seeded_mature_cluster):
    """Forks a real template from the mature cluster, returns its id."""
    from app.services.template_service import TemplateService
    tpl = await TemplateService().fork_from_cluster(seeded_mature_cluster, db_session)
    await db_session.flush()
    assert tpl is not None
    return tpl.id


@pytest.mark.asyncio
async def test_list_empty(app_client):
    resp = await app_client.get("/api/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_get_404_for_missing(app_client):
    resp = await app_client.get("/api/templates/does_not_exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fork_template_manual_idempotent(app_client, seeded_mature_cluster):
    cid = seeded_mature_cluster
    r1 = await app_client.post(f"/api/clusters/{cid}/fork-template")
    assert r1.status_code == 200, r1.text
    r2 = await app_client.post(f"/api/clusters/{cid}/fork-template")
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]


@pytest.mark.asyncio
async def test_retire_template(app_client, seeded_template_id):
    r = await app_client.post(f"/api/templates/{seeded_template_id}/retire")
    assert r.status_code == 200, r.text
    assert r.json()["retired_at"] is not None


@pytest.mark.asyncio
async def test_use_increments_usage(app_client, seeded_template_id):
    r = await app_client.post(f"/api/templates/{seeded_template_id}/use")
    assert r.status_code == 200, r.text
    assert r.json()["usage_count"] == 1


@pytest.fixture
async def reset_rate_limits():
    """Reset the module-level MemoryStorage singleton before/after this test.

    Opt-in only — do NOT mark autouse=True.  Other tests must not be affected.
    """
    from app.dependencies.rate_limit import _storage

    _storage.reset()
    yield
    _storage.reset()


@pytest.mark.asyncio
async def test_use_rate_limit_returns_429_on_31st_call(
    app_client, seeded_template_id, reset_rate_limits
):
    """30 rapid calls must all return 200; the 31st must be rejected with 429."""
    for _ in range(30):
        r = await app_client.post(f"/api/templates/{seeded_template_id}/use")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    r = await app_client.post(f"/api/templates/{seeded_template_id}/use")
    assert r.status_code == 429, f"Expected 429 on 31st call, got {r.status_code}"


@pytest.mark.asyncio
async def test_get_clusters_templates_returns_410(app_client):
    r = await app_client.get("/api/clusters/templates")
    assert r.status_code == 410
    body = r.json()
    # FastAPI wraps string details under {"detail": "..."}; dict details
    # come through as-is. Accept either shape.
    detail = body["detail"]
    if isinstance(detail, dict):
        detail = detail.get("detail", "")
    assert "GET /api/templates" in detail


@pytest.mark.asyncio
async def test_patch_cluster_state_template_returns_400(app_client, seeded_mature_cluster):
    r = await app_client.patch(
        f"/api/clusters/{seeded_mature_cluster}",
        json={"state": "template"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    if isinstance(detail, dict):
        detail = detail.get("detail", "")
    assert "fork-template" in detail
