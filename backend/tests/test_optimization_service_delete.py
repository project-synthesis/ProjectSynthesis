"""Tests for ``OptimizationService.delete_optimizations``.

Covers the cascade contract (DB-level ``ondelete="CASCADE"`` on the four
FKs referencing ``optimizations.id``), the ``PromptTemplate.source_optimization_id``
``SET NULL`` preservation, the ``optimization_deleted`` event emission,
and the ``affected_cluster_ids`` return value that callers use to kick
warm Phase 0 reconciliation.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    Feedback,
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
    PromptTemplate,
    RefinementBranch,
    RefinementTurn,
)
from app.services.event_bus import event_bus
from app.services.optimization_service import OptimizationService
from tests.conftest import drain_events_nonblocking


@pytest.fixture(autouse=True)
async def _enable_sqlite_fk_cascade(enable_sqlite_foreign_keys):
    """The shared ``db_session`` fixture uses ``sqlite+aiosqlite:///:memory:``
    without the backend's PRAGMA event hook — FK enforcement (and therefore
    ``ondelete="CASCADE"``) is OFF by default in SQLite. Delegates to the
    shared ``enable_sqlite_foreign_keys`` fixture so DB-level cascade
    behaves like production.
    """
    yield


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_opt_with_dependents(
    db_session,
    *,
    cluster_id: str,
    project_id: str | None = None,
    status: str = "completed",
) -> dict[str, str]:
    """Create one Optimization plus one row in every CASCADE-dependent table.

    Returns a dict of ids for later lookup assertions.
    """
    opt_id = str(uuid.uuid4())
    branch_id = str(uuid.uuid4())
    meta_pattern_id = str(uuid.uuid4())

    # Insert parent rows first (Optimization + MetaPattern) so FK dependents
    # always see a committed parent row — belt & braces against any flush
    # ordering surprise under the async session.
    db_session.add(Optimization(
        id=opt_id,
        raw_prompt="raw",
        optimized_prompt="opt",
        status=status,
        cluster_id=cluster_id,
        project_id=project_id,
    ))
    db_session.add(MetaPattern(
        id=meta_pattern_id,
        cluster_id=cluster_id,
        pattern_text="p",
    ))
    await db_session.commit()

    db_session.add_all([
        Feedback(optimization_id=opt_id, rating="thumbs_up"),
        OptimizationPattern(
            optimization_id=opt_id,
            cluster_id=cluster_id,
            meta_pattern_id=meta_pattern_id,
            relationship="source",
        ),
        RefinementBranch(id=branch_id, optimization_id=opt_id),
        RefinementTurn(
            optimization_id=opt_id,
            branch_id=branch_id,
            version=1,
            prompt="refined",
            strategy_used="auto",
        ),
    ])
    await db_session.commit()
    return {
        "opt_id": opt_id,
        "branch_id": branch_id,
        "meta_pattern_id": meta_pattern_id,
    }


async def test_cascades_all_dependents(db_session):
    """DELETE on optimizations removes Feedback, OptimizationPattern,
    RefinementBranch and RefinementTurn via DB cascade. No manual fan-out."""
    cluster_id = str(uuid.uuid4())
    db_session.add(PromptCluster(id=cluster_id, label="c", state="active"))
    await db_session.commit()
    ids = await _seed_opt_with_dependents(db_session, cluster_id=cluster_id)

    svc = OptimizationService(db_session)
    result = await svc.delete_optimizations([ids["opt_id"]], reason="test")

    assert result.deleted == 1
    assert result.affected_cluster_ids == {cluster_id}

    # Every dependent row must be gone.
    fb = (await db_session.execute(
        select(Feedback).where(Feedback.optimization_id == ids["opt_id"])
    )).scalar_one_or_none()
    op = (await db_session.execute(
        select(OptimizationPattern).where(
            OptimizationPattern.optimization_id == ids["opt_id"]
        )
    )).scalar_one_or_none()
    br = (await db_session.execute(
        select(RefinementBranch).where(RefinementBranch.id == ids["branch_id"])
    )).scalar_one_or_none()
    rt = (await db_session.execute(
        select(RefinementTurn).where(
            RefinementTurn.optimization_id == ids["opt_id"]
        )
    )).scalar_one_or_none()

    assert fb is None
    assert op is None
    assert br is None
    assert rt is None


async def test_nulls_template_source_optimization_id(db_session):
    """PromptTemplate.source_optimization_id is SET NULL, not CASCADE —
    templates are immutable forks and must outlive their source."""
    cluster_id = str(uuid.uuid4())
    opt_id = str(uuid.uuid4())
    db_session.add(PromptCluster(id=cluster_id, label="c", state="mature"))
    await db_session.commit()
    db_session.add(Optimization(id=opt_id, raw_prompt="raw", status="completed",
                                cluster_id=cluster_id))
    await db_session.commit()

    template = PromptTemplate(
        id=uuid.uuid4().hex,
        source_cluster_id=cluster_id,
        source_optimization_id=opt_id,
        project_id=None,
        label="Test Template",
        prompt="prompt body",
        strategy="auto",
        score=8.5,
        pattern_ids=[],
        domain_label="backend",
        promoted_at=_utcnow_naive(),
    )
    db_session.add(template)
    await db_session.commit()
    template_id = template.id

    svc = OptimizationService(db_session)
    result = await svc.delete_optimizations([opt_id], reason="test")
    assert result.deleted == 1

    # Template survives; source_optimization_id is NULL.
    # Expire the identity map so we re-read from the DB — the SET NULL
    # happened at the storage layer, the ORM may still be caching the
    # pre-delete attribute value.
    db_session.expire_all()
    kept = await db_session.get(PromptTemplate, template_id)
    assert kept is not None
    assert kept.source_optimization_id is None
    # Source cluster pointer is independent and should remain.
    assert kept.source_cluster_id == cluster_id


async def test_emits_event_per_deleted_row(db_session):
    """Each deleted row produces one `optimization_deleted` event carrying
    the id, cluster_id, project_id and reason."""
    cluster_a = str(uuid.uuid4())
    cluster_b = str(uuid.uuid4())
    db_session.add_all([
        PromptCluster(id=cluster_a, label="a", state="active"),
        PromptCluster(id=cluster_b, label="b", state="active"),
    ])
    await db_session.commit()

    id_a = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_a))["opt_id"]
    id_b = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_b))["opt_id"]

    # Defend against a prior test that may have flipped the process-level
    # bus into shutdown mode (publish is a no-op while shutting down).
    event_bus._shutting_down = False

    # Register a subscriber queue directly on the singleton event bus.
    # Using the public `subscribe()` async generator is racy — it only
    # registers on the first `__anext__()`, which can lose events fired
    # between "task started" and "generator entered its body". Hooking
    # the queue in directly is deterministic: the bus will broadcast to
    # it from the next publish onward.
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        svc = OptimizationService(db_session)
        result = await svc.delete_optimizations(
            [id_a, id_b], reason="bulk_reset",
        )
        assert result.deleted == 2

        # Drain the queue, filtering for our event type. Other tests may
        # leave the process-level bus in any state, so we tolerate extra
        # event types and only require the two we just produced.
        delete_events = [
            e for e in drain_events_nonblocking(queue)
            if e.get("event") == "optimization_deleted"
        ]
    finally:
        event_bus._subscribers.discard(queue)

    assert len(delete_events) == 2, (
        f"expected 2 optimization_deleted events; "
        f"got {len(delete_events)} (shutting_down="
        f"{event_bus._shutting_down}, subscribers={len(event_bus._subscribers)})"
    )
    seen_ids = {e["data"]["id"] for e in delete_events}
    assert seen_ids == {id_a, id_b}
    for e in delete_events:
        assert e["data"]["reason"] == "bulk_reset"
        assert e["data"]["cluster_id"] in {cluster_a, cluster_b}


async def test_returns_affected_cluster_ids(db_session):
    """affected_cluster_ids lets the caller publish taxonomy_changed so
    warm Phase 0 reconciles member_count on those clusters immediately."""
    cluster_a = str(uuid.uuid4())
    cluster_b = str(uuid.uuid4())
    db_session.add_all([
        PromptCluster(id=cluster_a, label="a", state="active"),
        PromptCluster(id=cluster_b, label="b", state="active"),
    ])
    await db_session.commit()

    id_a = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_a))["opt_id"]
    id_b1 = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_b))["opt_id"]
    id_b2 = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_b))["opt_id"]

    svc = OptimizationService(db_session)
    result = await svc.delete_optimizations([id_a, id_b1, id_b2])

    assert result.deleted == 3
    assert result.affected_cluster_ids == {cluster_a, cluster_b}


async def test_unknown_ids_are_silently_skipped(db_session):
    """Deleting ids that don't exist returns 0 and emits no events."""
    svc = OptimizationService(db_session)
    result = await svc.delete_optimizations(
        [str(uuid.uuid4()), str(uuid.uuid4())],
    )
    assert result.deleted == 0
    assert result.affected_cluster_ids == set()


async def test_empty_ids_is_noop(db_session):
    """Empty id list returns empty result without touching the DB."""
    svc = OptimizationService(db_session)
    result = await svc.delete_optimizations([])
    assert result.deleted == 0
    assert result.affected_cluster_ids == set()
    assert result.affected_project_ids == set()


async def test_publishes_taxonomy_changed_on_bulk_delete(db_session):
    """Bulk delete must publish a single `taxonomy_changed` event carrying
    the affected cluster ids so warm Phase 0 kicks in (I-0).

    Without this, `delete_optimizations` only marked clusters dirty on the
    in-process engine singleton — which is absent in MCP/CLI/test contexts.
    The SSE event is the cross-process signal that drives reconciliation.
    """
    cluster_a = str(uuid.uuid4())
    cluster_b = str(uuid.uuid4())
    db_session.add_all([
        PromptCluster(id=cluster_a, label="a", state="active"),
        PromptCluster(id=cluster_b, label="b", state="active"),
    ])
    await db_session.commit()

    id_a = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_a))["opt_id"]
    id_b = (await _seed_opt_with_dependents(db_session, cluster_id=cluster_b))["opt_id"]

    event_bus._shutting_down = False
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        svc = OptimizationService(db_session)
        result = await svc.delete_optimizations(
            [id_a, id_b], reason="bulk_reset",
        )
        assert result.deleted == 2

        taxonomy_events = [
            e for e in drain_events_nonblocking(queue)
            if e.get("event") == "taxonomy_changed"
        ]
    finally:
        event_bus._subscribers.discard(queue)

    assert len(taxonomy_events) == 1, (
        f"expected exactly 1 taxonomy_changed event; got {len(taxonomy_events)}"
    )
    payload = taxonomy_events[0]["data"]
    assert payload["reason"] == "bulk_reset"
    assert payload["trigger"] == "bulk_delete"
    assert set(payload["affected_clusters"]) == {cluster_a, cluster_b}


async def test_no_taxonomy_changed_event_when_nothing_deleted(db_session):
    """Unknown ids → zero rows deleted → no taxonomy_changed event."""
    event_bus._shutting_down = False
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    event_bus._subscribers.add(queue)
    try:
        svc = OptimizationService(db_session)
        result = await svc.delete_optimizations([str(uuid.uuid4())])
        assert result.deleted == 0

        taxonomy_events = [
            e for e in drain_events_nonblocking(queue)
            if e.get("event") == "taxonomy_changed"
        ]
    finally:
        event_bus._subscribers.discard(queue)

    assert taxonomy_events == []
