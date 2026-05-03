"""Cycle 7c RED → GREEN: ProbeService routes status writes through WriteQueue.

The probe service has 8+ ``self.db.commit()`` / ``db.commit()`` sites
spanning the 5-phase orchestrator + lifecycle helpers. Cycle 7c migrates
each to a ``submit()`` callback so probe row mutations (status
transitions, terminal writes, cancellation marks, persistence) serialize
against every other backend writer through the single-writer queue.

Test design constraint: the full ProbeService.run() pipeline pulls in
the entire batch_pipeline + repo_index + embedding stack — heavy
infrastructure that this cycle's RED tests don't actually need to
exercise. The tests below pin the WIRING of the queue (constructor
acceptance + helper dispatch) without requiring a fully-running probe
session.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.probe_service import ProbeService

# ---------------------------------------------------------------------------
# Constructor + helper dispatch
# ---------------------------------------------------------------------------


class TestProbeServiceQueueWiring:
    """ProbeService accepts ``write_queue=`` and routes status writes via it."""

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    def test_constructor_accepts_write_queue_kwarg(self):
        """RED: ProbeService.__init__ must accept ``write_queue=``.

        Pre-GREEN this fails with ``TypeError: __init__() got an
        unexpected keyword argument 'write_queue'`` — the v0.4.12
        signature is ``(db, provider, repo_query, context_service,
        event_bus, embedding_service=None, session_factory=None)``.

        Post-GREEN the queue is stored on ``self.write_queue`` so the
        helper methods + routers can route through it.
        """
        db = MagicMock()
        provider = MagicMock()
        repo_query = MagicMock()
        context_service = MagicMock()
        event_bus = MagicMock()
        write_queue = MagicMock()

        svc = ProbeService(
            db=db,
            provider=provider,
            repo_query=repo_query,
            context_service=context_service,
            event_bus=event_bus,
            write_queue=write_queue,
        )
        assert svc.write_queue is write_queue

    def test_constructor_default_write_queue_is_none(self):
        """Backward compat: not supplying write_queue keeps it as ``None``
        so legacy callers + tests continue to use the direct ``self.db``
        path."""
        svc = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
        )
        assert getattr(svc, "write_queue", "missing") is None

    @pytest.mark.asyncio
    async def test_set_probe_status_helper_routes_through_queue(self):
        """RED: the ``_set_probe_status(probe_id, status, ...)`` helper
        must submit a callback labelled ``probe_status_transition`` when
        ``self.write_queue`` is set.

        Pre-GREEN the helper does not exist on ProbeService.
        """
        captured: list[tuple[str | None, Any]] = []

        # Minimal write_queue that records submits and runs the callback
        # against an inline mock session (the helper only needs the row
        # update to no-op for label assertion).
        async def _fake_submit(work, *, timeout=None, operation_label=None):
            captured.append((operation_label, work))
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=MagicMock(status="running"))
            return await work(mock_db)

        write_queue = MagicMock()
        write_queue.submit = _fake_submit

        svc = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
            write_queue=write_queue,
        )
        await svc._set_probe_status("test-probe-id", "generating")
        labels = [c[0] for c in captured]
        assert "probe_status_transition" in labels


class TestProbeServiceWriteQueueDependency:
    """``app.dependencies.probes.build_probe_service`` accepts + threads
    ``write_queue=`` so the REST router (cycle 7c) can wire it via DI."""

    def test_build_probe_service_accepts_write_queue(self):
        """RED: ``build_probe_service`` must thread ``write_queue=`` to
        the constructed ProbeService."""
        from app.dependencies.probes import build_probe_service

        db = MagicMock()
        provider = MagicMock()
        context_service = MagicMock()
        write_queue = MagicMock()

        svc = build_probe_service(
            db=db,
            provider=provider,
            context_service=context_service,
            write_queue=write_queue,
        )
        assert svc.write_queue is write_queue


class TestProbeServiceCancellation:
    """Cancellation hardening: when ``self.write_queue`` is set,
    ``_mark_cancelled`` writes through the queue under ``asyncio.shield``
    so a re-cancel doesn't lose the terminal-state write.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_mark_cancelled_routes_through_queue_when_set(self):
        """RED: ``_mark_cancelled`` with ``write_queue`` set must produce
        a submit() with operation_label ``probe_mark_cancelled``."""
        captured: list[str | None] = []

        async def _fake_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            mock_db = AsyncMock()
            return await work(mock_db)

        write_queue = MagicMock()
        write_queue.submit = _fake_submit

        svc = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
            write_queue=write_queue,
        )

        # Build a fake row — _mark_cancelled mutates it then commits.
        # Under the queue path, the helper looks up the row inside
        # the callback so the in-memory ``row`` arg is only a hint.
        row = MagicMock()
        await svc._mark_cancelled(row, "test-probe-id")

        assert "probe_mark_cancelled" in captured
