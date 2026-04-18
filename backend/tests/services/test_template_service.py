from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.models import Optimization, PromptCluster, PromptTemplate
from app.services.template_service import TemplateService  # expected to fail import


async def _seed_cluster_with_opt(db, *, score: float = 7.5, label: str = "tpl") -> tuple[str, str]:
    cluster = PromptCluster(id=uuid.uuid4().hex, label=label, state="mature")
    db.add(cluster)
    await db.flush()
    opt = Optimization(
        id=uuid.uuid4().hex,
        cluster_id=cluster.id,
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=score,
    )
    db.add(opt)
    await db.flush()
    return cluster.id, opt.id


@pytest.mark.asyncio
async def test_fork_from_cluster_creates_template_and_increments_count(db_session):
    cluster_id, opt_id = await _seed_cluster_with_opt(db_session)
    svc = TemplateService()
    tpl = await svc.fork_from_cluster(cluster_id, db_session)
    assert tpl is not None
    assert tpl.source_cluster_id == cluster_id
    assert tpl.source_optimization_id == opt_id
    assert tpl.retired_at is None
    assert tpl.usage_count == 0
    cluster = (await db_session.execute(
        select(PromptCluster).where(PromptCluster.id == cluster_id)
    )).scalar_one()
    assert cluster.template_count == 1


@pytest.mark.asyncio
async def test_fork_idempotent_same_optimization(db_session):
    cluster_id, opt_id = await _seed_cluster_with_opt(db_session)
    svc = TemplateService()
    tpl1 = await svc.fork_from_cluster(cluster_id, db_session)
    tpl2 = await svc.fork_from_cluster(cluster_id, db_session)
    assert tpl1.id == tpl2.id
    cluster = (await db_session.execute(
        select(PromptCluster).where(PromptCluster.id == cluster_id)
    )).scalar_one()
    assert cluster.template_count == 1


@pytest.mark.asyncio
async def test_fork_no_optimizations_returns_none(db_session):
    cluster = PromptCluster(id=uuid.uuid4().hex, label="x", state="mature")
    db_session.add(cluster)
    await db_session.flush()
    svc = TemplateService()
    tpl = await svc.fork_from_cluster(cluster.id, db_session)
    assert tpl is None


@pytest.mark.asyncio
async def test_fork_idempotent_under_concurrent_inserts(tmp_path):
    """Spec §Q1: the partial unique index must enforce single-live-template per
    source even when 10 independent sessions race. Losers catch IntegrityError
    and return the winning row.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    db_path = tmp_path / "race.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed cluster + optimization (one session, committed before the race)
    async with session_factory() as seed:
        cluster = PromptCluster(id="c_race", label="x", state="mature", template_count=0)
        opt = Optimization(
            id=uuid.uuid4().hex, cluster_id="c_race",
            raw_prompt="r", optimized_prompt="o",
            strategy_used="auto", overall_score=7.5,
        )
        seed.add_all([cluster, opt])
        await seed.commit()

    svc = TemplateService()

    async def _one_fork():
        async with session_factory() as s:
            tpl = await svc.fork_from_cluster("c_race", s)
            await s.commit()
            return tpl.id if tpl else None

    results = await asyncio.gather(*[_one_fork() for _ in range(10)])
    assert len({r for r in results if r}) == 1, f"10 sessions produced {len(set(results))} distinct templates"

    async with session_factory() as verify:
        count = (await verify.execute(
            select(PromptTemplate).where(
                PromptTemplate.source_cluster_id == "c_race",
                PromptTemplate.retired_at.is_(None),
            )
        )).scalars().all()
        assert len(count) == 1
        cluster = (await verify.execute(
            select(PromptCluster).where(PromptCluster.id == "c_race")
        )).scalar_one()
        assert cluster.template_count == 1


@pytest.mark.asyncio
async def test_fork_preserves_domain_label_after_reparent(db_session):
    # seed domain tree: project → backend (domain) → cluster
    proj = PromptCluster(id="p1", label="p", state="project")
    dom = PromptCluster(id="d1", label="backend", state="domain", parent_id="p1")
    cluster = PromptCluster(id="c1", label="auth", state="mature", parent_id="d1")
    db_session.add_all([proj, dom, cluster])
    await db_session.flush()
    opt = Optimization(
        id=uuid.uuid4().hex, cluster_id="c1",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.8,
    )
    db_session.add(opt)
    await db_session.flush()

    svc = TemplateService()
    tpl = await svc.fork_from_cluster("c1", db_session)
    assert tpl.domain_label == "backend"

    # Re-parent cluster under a different domain
    dom2 = PromptCluster(id="d2", label="data", state="domain", parent_id="p1")
    db_session.add(dom2)
    cluster.parent_id = "d2"
    await db_session.flush()

    # Frozen domain_label must NOT change
    reloaded = (await db_session.execute(
        select(PromptTemplate).where(PromptTemplate.id == tpl.id)
    )).scalar_one()
    assert reloaded.domain_label == "backend"


@pytest.mark.asyncio
async def test_fork_walks_past_sub_domain_to_domain(db_session):
    """Three-level hierarchy: cluster → sub-domain → domain → project.

    Frozen domain_label must be the TOP-LEVEL domain label ("backend"),
    not the intermediate sub-domain label ("auth"). This exercises the
    ancestor walk past non-domain-state intermediate nodes.
    """
    proj = PromptCluster(id="p1", label="p", state="project")
    dom = PromptCluster(id="d_backend", label="backend", state="domain", parent_id="p1")
    # Sub-domain uses state="sub_domain" per the taxonomy hierarchy spec.
    sub = PromptCluster(id="sd_auth", label="auth", state="sub_domain", parent_id="d_backend")
    cluster = PromptCluster(id="c_leaf", label="jwt", state="mature", parent_id="sd_auth")
    db_session.add_all([proj, dom, sub, cluster])
    await db_session.flush()
    opt = Optimization(
        id=uuid.uuid4().hex, cluster_id="c_leaf",
        raw_prompt="r", optimized_prompt="o",
        strategy_used="auto", overall_score=7.8,
    )
    db_session.add(opt)
    await db_session.flush()

    svc = TemplateService()
    tpl = await svc.fork_from_cluster("c_leaf", db_session)
    assert tpl is not None
    assert tpl.domain_label == "backend", (
        f"expected frozen label 'backend' (top-level domain), got {tpl.domain_label!r} "
        "— walk may be terminating at sub-domain or failing to traverse past it"
    )


@pytest.mark.asyncio
async def test_fork_race_recovery_returns_winner_deterministically(tmp_path):
    """Deterministic coverage of the IntegrityError recovery branch.

    The 10-session concurrent test proves the *outcome* (one live template),
    but SQLite's file lock may serialize the sessions so the
    ``except IntegrityError`` branch in ``fork_from_cluster`` is never
    actually executed. This test forces that branch by pre-committing a
    winner row and then patching the service's pre-check SELECT to return
    ``None`` — driving the service past the happy-path short-circuit into
    the INSERT, which then collides with the partial unique index.

    Query-order contract (coupled to ``template_service.fork_from_cluster``):
      1. SELECT PromptCluster (cluster load)
      2. SELECT Optimization (top_opt load)
      3. SELECT PromptTemplate (pre-check for existing live template)
      4. SELECT OptimizationPattern.meta_pattern_id (pattern_ids)
      5. flush() → INSERT PromptTemplate (raises IntegrityError here)
      6. After rollback: SELECT PromptTemplate (winner re-read)

    We hijack call #3 only. If the service's query order changes, this
    test will fail loudly with an assertion naming the actual returned id.
    """
    from unittest.mock import patch

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    db_path = tmp_path / "race_det.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Seed cluster + optimization.
    async with session_factory() as seed:
        cluster = PromptCluster(id="c_rd", label="x", state="mature", template_count=0)
        opt = Optimization(
            id="o_rd", cluster_id="c_rd",
            raw_prompt="r", optimized_prompt="o",
            strategy_used="auto", overall_score=7.5,
        )
        seed.add_all([cluster, opt])
        await seed.commit()

    svc = TemplateService()

    # Session A: full fork + commit → becomes the race winner.
    async with session_factory() as session_a:
        tpl_a = await svc.fork_from_cluster("c_rd", session_a)
        await session_a.commit()
        assert tpl_a is not None
        winner_id = tpl_a.id

    # Session B: patch the pre-check SELECT (call #3) to return a result
    # whose .scalar_one_or_none() yields None. The service will then
    # proceed to INSERT, collide on the partial unique index, catch
    # IntegrityError, rollback, re-select, and return the winner.
    async with session_factory() as session_b:
        real_execute = session_b.execute
        call_count = {"n": 0}

        class _ForceNone:
            def scalar_one_or_none(self):
                return None

        async def _fake_execute(stmt, *args, **kwargs):
            call_count["n"] += 1
            # Call #3 is the pre-check PromptTemplate SELECT (see docstring
            # query-order contract). Force it to report no existing row.
            if call_count["n"] == 3:
                return _ForceNone()
            return await real_execute(stmt, *args, **kwargs)

        with patch.object(session_b, "execute", side_effect=_fake_execute):
            tpl_b = await svc.fork_from_cluster("c_rd", session_b)

        assert tpl_b is not None, (
            "IntegrityError recovery returned None — winner re-SELECT failed; "
            "check service's post-rollback query path"
        )
        assert tpl_b.id == winner_id, (
            f"IntegrityError path should return the winner {winner_id!r}, "
            f"got {tpl_b.id!r} — query-order contract may have shifted"
        )

    await engine.dispose()
