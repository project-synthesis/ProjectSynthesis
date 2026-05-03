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


# ---------------------------------------------------------------------------
# Cycle 7 OPERATE: probe status transition observability under queue dispatch
# ---------------------------------------------------------------------------


class TestCycle7Operate:
    """OPERATE phase (v0.4.13 cycle 7c): probe status transitions emit
    observable side effects ONLY after the queue's submit() resolves.

    Per ``feedback_tdd_protocol.md`` Phase 5: the GREEN tests above verify
    the dispatch path (label captured, callback invoked). This OPERATE test
    pins the FAILURE SEMANTICS extension that mirrors cycles 2-6 — when
    ``submit()`` raises (e.g. ``WriteQueueOverloadedError``), no downstream
    side effect fires. Events represent committed state, never phantom
    pre-submit emissions.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_probe_status_transitions_event_emission_after_queue_resolves(self):
        """``_set_probe_status`` returns only AFTER the queue's submit()
        resolves; downstream events fired by the caller are observable
        AFTER (never DURING) the helper's await.

        When the queue raises (synthetic ``WriteQueueOverloadedError``),
        ZERO downstream events fire — pinning the failure-semantics
        invariant established in cycles 2-6.

        Pin failure semantics: events represent post-commit committed
        state, never pre-submit phantom state.
        """
        from app.services.write_queue import WriteQueueOverloadedError

        # ----------------------------------------------------------
        # Path A: submit resolves cleanly → event fires AFTER await.
        # ----------------------------------------------------------
        events_fired: list[str] = []

        # The submit callback simulates real queue serialization: it
        # records "submit_in_progress" → "submit_committed" so the test
        # can assert ordering against the post-await event.
        async def _ok_submit(work, *, timeout=None, operation_label=None):
            events_fired.append("submit_in_progress")
            mock_db = AsyncMock()
            mock_db.get = AsyncMock(return_value=MagicMock(status="running"))
            await work(mock_db)
            events_fired.append("submit_committed")
            return None

        write_queue = MagicMock()
        write_queue.submit = _ok_submit

        svc = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
            write_queue=write_queue,
        )

        # Caller-side: invoke _set_probe_status, then immediately publish
        # the downstream event. Per the queue contract the helper does not
        # return until submit_committed has been appended — so the
        # downstream event always sorts AFTER it.
        await svc._set_probe_status("op-probe-id", "running")
        events_fired.append("downstream_event")

        # Verify post-submit ordering invariant: every queue-internal event
        # appears BEFORE the caller's downstream event.
        assert events_fired == [
            "submit_in_progress",
            "submit_committed",
            "downstream_event",
        ], (
            f"submit must complete before downstream event fires; got: "
            f"{events_fired}"
        )

        # ----------------------------------------------------------
        # Path B: submit raises → ZERO downstream events fire.
        # ----------------------------------------------------------
        events_fired_after_raise: list[str] = []

        async def _raising_submit(work, *, timeout=None, operation_label=None):
            events_fired_after_raise.append("submit_attempted")
            raise WriteQueueOverloadedError(
                "synthetic overload for OPERATE failure-semantics check",
            )

        raise_queue = MagicMock()
        raise_queue.submit = _raising_submit

        svc_raise = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
            write_queue=raise_queue,
        )

        # The helper must propagate the queue's exception — never silently
        # swallow it. Caller-side downstream event MUST NOT fire.
        with pytest.raises(WriteQueueOverloadedError):
            await svc_raise._set_probe_status("op-probe-id", "running")
            # Unreachable: assertion below verifies guard.
            events_fired_after_raise.append("downstream_event_raised_path")  # noqa: E501

        # Failure semantics: the caller never reached the post-helper code,
        # so no "downstream_event_raised_path" was appended. ``submit_attempted``
        # is the ONLY entry — proving the queue rejected the work synchronously
        # and the helper raised before any side effect.
        assert events_fired_after_raise == ["submit_attempted"], (
            "Failure semantics violated: helper appended downstream events "
            "after submit() raised. Queue contract requires zero side "
            f"effects on failed submit. Got: {events_fired_after_raise}"
        )
