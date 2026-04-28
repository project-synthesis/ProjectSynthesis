"""v0.4.11 P1 — Operator dissolve-empty endpoint, engine-method tests.

The engine method ``TaxonomyEngine.dissolve_empty_domain(db, domain_id)``
is the operator escape hatch for ghost domains: empty top-level domains
that the standard 48h ``DOMAIN_DISSOLUTION_MIN_AGE_HOURS`` gate has
frozen in place. The method enforces a much shorter
``DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES`` floor (default 30 min) so
the warm path can still create domains during organic emergence without
this surface racing them out.

Returned envelope:
  - ``dissolved=True, reason=None`` on successful dissolution.
  - ``dissolved=False, reason='already_dissolved'`` on idempotent re-call.
  - ``dissolved=False, reason='not_empty'`` when ``member_count > 0``.
  - ``dissolved=False, reason='too_young'`` when age < floor.

Telemetry: emits a ``domain_ghost_dissolved`` decision event on success,
plus a cross-process ``taxonomy_changed`` SSE event.

Pre-fix expectation (RED): every test below fails with ``ImportError``
(constant absent), ``AttributeError`` (engine method absent), or schema
validation errors.

Spec: ``docs/specs/domain-proposal-hardening-2026-04-28.md`` §P1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.models import PromptCluster
from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES
from app.services.taxonomy.cluster_meta import write_meta
from app.services.taxonomy.event_logger import (
    TaxonomyEventLogger,
    set_event_logger,
)

EMBEDDING_DIM = 384


def _random_embedding(seed: int = 0) -> bytes:
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-9
    return vec.tobytes()


def _make_general(label: str = "general") -> PromptCluster:
    """Build the canonical general node — required dissolution target."""
    return PromptCluster(
        label=label,
        state="domain",
        domain=label,
        task_type="general",
        parent_id=None,
        persistence=1.0,
        color_hex="#7a7a9e",
        centroid_embedding=_random_embedding(0),
        cluster_metadata=write_meta(None, source="seed"),
        member_count=0,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_ghost_domain(
    label: str,
    *,
    age_minutes: int,
    member_count: int = 0,
) -> PromptCluster:
    """Build a top-level domain node with a controllable age + size."""
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=age_minutes,
    )
    return PromptCluster(
        label=label,
        state="domain",
        domain=label,
        task_type="general",
        parent_id=None,
        persistence=1.0,
        color_hex="#aabbcc",
        centroid_embedding=_random_embedding(hash(label) % 2**31),
        cluster_metadata=write_meta(None, source="discovered"),
        member_count=member_count,
        created_at=created,
    )


def _make_engine(mock_provider):
    """Build a TaxonomyEngine with stubbed indices for dissolution tests.

    ``_dissolve_node`` awaits ``idx.remove(node.id)`` on every index
    (embedding/transformation/optimized/qualifier), so each must be an
    AsyncMock-backed stub or the dissolution path will TypeError on the
    sync MagicMock return.
    """
    from app.services.taxonomy.engine import TaxonomyEngine

    mock_embedding = AsyncMock()
    engine = TaxonomyEngine(
        embedding_service=mock_embedding,
        provider=mock_provider,
    )
    for attr in (
        "_embedding_index",
        "_transformation_index",
        "_optimized_index",
        "_qualifier_index",
    ):
        mock_idx = MagicMock()
        mock_idx.remove = AsyncMock()
        setattr(engine, attr, mock_idx)
    return engine


@pytest.fixture(autouse=True)
def _setup_event_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Bind a fresh per-test logger so log_decision() doesn't raise + tests
    can assert on the ring buffer."""
    logger = TaxonomyEventLogger(events_dir=tmp_path, publish_to_bus=False)
    set_event_logger(logger)
    return logger


# ---------------------------------------------------------------------------
# Tests — TestDissolveEmptyDomain (4 service-level ACs)
# ---------------------------------------------------------------------------


class TestDissolveEmptyDomain:
    """v0.4.11 P1 engine-method contract for ``dissolve_empty_domain``."""

    @pytest.mark.asyncio
    async def test_dissolve_empty_success(
        self, db, mock_provider, _setup_event_logger,
    ):
        """AC-P1-1: empty domain (member_count=0) older than the
        ``DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES`` floor is dissolved
        successfully and the row transitions to ``state='archived'``.
        """
        engine = _make_engine(mock_provider)

        general = _make_general()
        db.add(general)
        await db.flush()

        ghost = _make_ghost_domain("ghostdomain", age_minutes=31)
        db.add(ghost)
        await db.flush()
        ghost_id = ghost.id

        result = await engine.dissolve_empty_domain(db, ghost_id)

        assert result["dissolved"] is True, (
            f"expected dissolved=True for an aged empty ghost. "
            f"Got: {result!r}"
        )
        assert result["reason"] is None, (
            f"reason must be None on success. Got: {result['reason']!r}"
        )
        assert result["domain_id"] == ghost_id
        assert result["domain_label"] == "ghostdomain"
        assert result["age_hours"] >= 30 / 60.0, (
            f"age_hours must reflect the configured age. "
            f"Got: {result['age_hours']}"
        )

        # Row must transition to archived (the post-dissolution sentinel).
        # The engine method does NOT commit (the router does); use a fresh
        # SELECT instead of db.refresh() to observe the in-session change
        # without reverting it from the unwritten DB.
        from sqlalchemy import select  # noqa: PLC0415
        reload_q = await db.execute(
            select(PromptCluster).where(PromptCluster.id == ghost_id),
        )
        reloaded = reload_q.scalar_one()
        assert reloaded.state == "archived", (
            f"dissolved domain must be archived (not deleted). "
            f"Got state={reloaded.state!r}"
        )
        assert reloaded.member_count == 0
        # Both archived + sub-domain candidates should be in the
        # excluded-from-active-queries frozenset.
        assert "archived" in EXCLUDED_STRUCTURAL_STATES

        # Forensic event must land in the ring buffer.
        recent = _setup_event_logger.get_recent(limit=100)
        ghost_events = [
            ev for ev in recent
            if ev.get("decision") == "domain_ghost_dissolved"
        ]
        assert ghost_events, (
            "expected a domain_ghost_dissolved event. "
            f"Got decisions: {[ev.get('decision') for ev in recent]}"
        )
        ctx = ghost_events[0].get("context") or {}
        assert ctx.get("domain_label") == "ghostdomain"
        assert ctx.get("dissolution_path") == "operator_ghost_dissolve"

    @pytest.mark.asyncio
    async def test_dissolve_with_members_blocked(
        self, db, mock_provider,
    ):
        """AC-P1-2: a domain with member_count > 0 must NOT be dissolved.

        The member-count gate is the central invariant of the operator
        hatch — only ghost domains qualify. The row must remain in
        ``state='domain'`` and the response carries
        ``reason='not_empty'``.
        """
        engine = _make_engine(mock_provider)

        general = _make_general()
        db.add(general)
        await db.flush()

        populated = _make_ghost_domain(
            "populated", age_minutes=120, member_count=3,
        )
        db.add(populated)
        await db.flush()
        domain_id = populated.id

        result = await engine.dissolve_empty_domain(db, domain_id)

        assert result["dissolved"] is False, (
            f"populated domain must not be dissolved. Got: {result!r}"
        )
        assert result["reason"] == "not_empty", (
            f"reason must be 'not_empty'. Got: {result['reason']!r}"
        )
        assert result["domain_label"] == "populated"

        # Row must still exist as a domain.
        from sqlalchemy import select  # noqa: PLC0415
        q = await db.execute(
            select(PromptCluster).where(PromptCluster.id == domain_id),
        )
        reloaded = q.scalar_one()
        assert reloaded.state == "domain"
        assert reloaded.member_count == 3

    @pytest.mark.asyncio
    async def test_dissolve_too_young_blocked(
        self, db, mock_provider,
    ):
        """AC-P1-3: empty domain younger than
        ``DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES`` (default 30 min) is
        blocked with ``reason='too_young'`` so newly-promoted domains
        don't get instantly dissolved during organic emergence.
        """
        engine = _make_engine(mock_provider)

        general = _make_general()
        db.add(general)
        await db.flush()

        # 15 min age — below the 30-min floor.
        infant = _make_ghost_domain("infant", age_minutes=15)
        db.add(infant)
        await db.flush()
        infant_id = infant.id

        result = await engine.dissolve_empty_domain(db, infant_id)

        assert result["dissolved"] is False, (
            f"too-young domain must not be dissolved. Got: {result!r}"
        )
        assert result["reason"] == "too_young", (
            f"reason must be 'too_young'. Got: {result['reason']!r}"
        )

        from sqlalchemy import select  # noqa: PLC0415
        q = await db.execute(
            select(PromptCluster).where(PromptCluster.id == infant_id),
        )
        reloaded = q.scalar_one()
        assert reloaded.state == "domain", (
            f"too-young domain row must remain unchanged. "
            f"Got state={reloaded.state!r}"
        )

    @pytest.mark.asyncio
    async def test_dissolve_idempotent(
        self, db, mock_provider,
    ):
        """AC-P1-5: a second call against the same id (now archived)
        returns ``dissolved=False, reason='already_dissolved'`` rather
        than raising.

        Idempotency lets operators safely retry on flaky network without
        double-emitting events or attempting a second dissolution.
        """
        engine = _make_engine(mock_provider)

        general = _make_general()
        db.add(general)
        await db.flush()

        ghost = _make_ghost_domain("ghostidempotent", age_minutes=31)
        db.add(ghost)
        await db.flush()
        ghost_id = ghost.id

        first = await engine.dissolve_empty_domain(db, ghost_id)
        assert first["dissolved"] is True, (
            f"first call must dissolve. Got: {first!r}"
        )

        # Second call must be a no-op idempotent return.
        second = await engine.dissolve_empty_domain(db, ghost_id)
        assert second["dissolved"] is False, (
            f"second call must not re-dissolve. Got: {second!r}"
        )
        assert second["reason"] == "already_dissolved", (
            f"reason must be 'already_dissolved'. "
            f"Got: {second['reason']!r}"
        )
        assert second["domain_id"] == ghost_id
