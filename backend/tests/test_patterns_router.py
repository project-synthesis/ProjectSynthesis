"""Tests for the /api/patterns pattern-graph router (Hybrid per-project view)."""

from __future__ import annotations

import pytest

from app.models import (
    GlobalPattern,
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
)


@pytest.mark.asyncio
async def test_empty_returns_empty_lists(app_client, db_session):
    """Fresh DB returns empty meta + global arrays (200, valid envelope)."""
    resp = await app_client.get("/api/patterns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta_patterns"] == []
    assert body["global_patterns"] == []
    assert body["project_id"] is None


@pytest.mark.asyncio
async def test_global_view_returns_all_meta_patterns(app_client, db_session):
    """Without project_id, all meta-patterns are returned (global view)."""
    cluster = PromptCluster(
        id="c-global",
        label="stuff",
        state="active",
        domain="backend",
        task_type="coding",
        centroid_embedding=b"\x00" * 384,
    )
    db_session.add(cluster)
    await db_session.flush()

    mp = MetaPattern(
        cluster_id=cluster.id,
        pattern_text="use guards",
        source_count=4,
    )
    db_session.add(mp)
    await db_session.commit()

    resp = await app_client.get("/api/patterns")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["meta_patterns"]) == 1
    node = body["meta_patterns"][0]
    assert node["pattern_text"] == "use guards"
    assert node["source_count"] == 4
    assert node["cluster_label"] == "stuff"
    assert node["domain"] == "backend"


@pytest.mark.asyncio
async def test_project_scoped_filters_by_optimization_provenance(
    app_client, db_session,
):
    """With project_id, only patterns with matching project evidence appear."""
    proj_a = PromptCluster(
        id="p-a", label="A", state="project",
        domain="general", task_type="general", member_count=0,
    )
    proj_b = PromptCluster(
        id="p-b", label="B", state="project",
        domain="general", task_type="general", member_count=0,
    )
    db_session.add_all([proj_a, proj_b])
    await db_session.flush()

    cluster_a = PromptCluster(
        id="c-a", label="A cluster", state="active",
        domain="backend", task_type="coding",
        centroid_embedding=b"\x00" * 384,
    )
    cluster_b = PromptCluster(
        id="c-b", label="B cluster", state="active",
        domain="frontend", task_type="coding",
        centroid_embedding=b"\x00" * 384,
    )
    db_session.add_all([cluster_a, cluster_b])
    await db_session.flush()

    mp_a = MetaPattern(
        cluster_id=cluster_a.id,
        pattern_text="A technique",
        source_count=3,
    )
    mp_b = MetaPattern(
        cluster_id=cluster_b.id,
        pattern_text="B technique",
        source_count=3,
    )
    db_session.add_all([mp_a, mp_b])
    await db_session.flush()

    opt_a = Optimization(
        raw_prompt="a",
        status="completed",
        cluster_id=cluster_a.id,
        project_id=proj_a.id,
    )
    opt_b = Optimization(
        raw_prompt="b",
        status="completed",
        cluster_id=cluster_b.id,
        project_id=proj_b.id,
    )
    db_session.add_all([opt_a, opt_b])
    await db_session.flush()

    db_session.add_all([
        OptimizationPattern(
            optimization_id=opt_a.id,
            cluster_id=cluster_a.id,
            meta_pattern_id=mp_a.id,
            relationship="source",
            similarity=0.9,
        ),
        OptimizationPattern(
            optimization_id=opt_b.id,
            cluster_id=cluster_b.id,
            meta_pattern_id=mp_b.id,
            relationship="source",
            similarity=0.9,
        ),
    ])
    await db_session.commit()

    # Project A should see only its own pattern.
    resp_a = await app_client.get(f"/api/patterns?project_id={proj_a.id}")
    assert resp_a.status_code == 200
    texts_a = {m["pattern_text"] for m in resp_a.json()["meta_patterns"]}
    assert texts_a == {"A technique"}

    # Project B should see only its own pattern.
    resp_b = await app_client.get(f"/api/patterns?project_id={proj_b.id}")
    assert resp_b.status_code == 200
    texts_b = {m["pattern_text"] for m in resp_b.json()["meta_patterns"]}
    assert texts_b == {"B technique"}


@pytest.mark.asyncio
async def test_global_patterns_always_included(app_client, db_session):
    """GlobalPattern rows are cross-project by design — always returned."""
    proj = PromptCluster(
        id="p-only", label="only", state="project",
        domain="general", task_type="general", member_count=0,
    )
    db_session.add(proj)
    await db_session.flush()

    gp = GlobalPattern(
        pattern_text="universal rule",
        source_cluster_ids=["c1", "c2"],
        source_project_ids=["p1", "p2"],
        cross_project_count=2,
        avg_cluster_score=8.0,
        state="active",
    )
    db_session.add(gp)
    await db_session.commit()

    for url in ("/api/patterns", f"/api/patterns?project_id={proj.id}"):
        resp = await app_client.get(url)
        assert resp.status_code == 200
        body = resp.json()
        assert any(
            g["pattern_text"] == "universal rule"
            for g in body["global_patterns"]
        ), f"global pattern missing from {url}"


@pytest.mark.asyncio
async def test_retired_global_patterns_excluded(app_client, db_session):
    """Only ``state='active'`` GlobalPatterns surface in the graph view."""
    db_session.add_all([
        GlobalPattern(
            pattern_text="active one",
            source_cluster_ids=["c1"],
            source_project_ids=["p1"],
            cross_project_count=1,
            state="active",
        ),
        GlobalPattern(
            pattern_text="retired one",
            source_cluster_ids=["c2"],
            source_project_ids=["p2"],
            cross_project_count=1,
            state="retired",
        ),
    ])
    await db_session.commit()

    resp = await app_client.get("/api/patterns")
    assert resp.status_code == 200
    texts = {g["pattern_text"] for g in resp.json()["global_patterns"]}
    assert "active one" in texts
    assert "retired one" not in texts


@pytest.mark.asyncio
async def test_limit_query_param(app_client, db_session):
    """``limit`` caps the meta_patterns result count."""
    cluster = PromptCluster(
        id="c-limit", label="limit", state="active",
        domain="backend", task_type="coding",
        centroid_embedding=b"\x00" * 384,
    )
    db_session.add(cluster)
    await db_session.flush()

    for i in range(5):
        db_session.add(MetaPattern(
            cluster_id=cluster.id,
            pattern_text=f"pattern {i}",
            source_count=i + 1,
        ))
    await db_session.commit()

    resp = await app_client.get("/api/patterns?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()["meta_patterns"]) == 2
