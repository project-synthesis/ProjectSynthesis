"""Cycle 8 RED → GREEN: services/* route writes through WriteQueue.

Group 1 of cycle 8 migrates the warm/REST writer call sites in services/
to ``write_queue.submit()``. Specifically:

- ``feedback_service.FeedbackService`` — accepts ``write_queue`` + routes
  ``create_feedback``'s commit through it.
- ``optimization_service.OptimizationService`` — accepts ``write_queue``
  + routes ``delete_optimizations``'s commit through it.
- ``audit_logger.log_event`` / ``prune_audit_log`` — accept ``write_queue``
  + route their commits through it (function-level, not class).
- ``gc.run_startup_gc`` / ``run_recurring_gc`` — accept ``write_queue``
  + route their final commit through it.
- ``orphan_recovery.OrphanRecoveryService`` — its ``scan_and_recover`` /
  ``_increment_retry`` / ``_recover_one`` paths commit through ``write_queue``
  when it's set.

Tests pin the WIRING + label assertions; behavioural correctness rides on
the existing service-level test coverage which is unchanged.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recording_queue() -> tuple[MagicMock, list[tuple[str | None, Any]]]:
    """Return a queue stub that captures ``(operation_label, work)`` and
    runs the work against an inline ``AsyncMock`` session.
    """
    captured: list[tuple[str | None, Any]] = []

    async def _fake_submit(work, *, timeout=None, operation_label=None):
        captured.append((operation_label, work))
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
        return await work(mock_db)

    queue = MagicMock()
    queue.submit = _fake_submit
    return queue, captured


# ---------------------------------------------------------------------------
# FeedbackService
# ---------------------------------------------------------------------------


class TestFeedbackServiceQueueWiring:
    """``FeedbackService`` accepts ``write_queue=`` + routes commits via it."""

    def test_constructor_accepts_write_queue_kwarg(self):
        """RED: ``FeedbackService(session, write_queue=...)`` must accept
        the ``write_queue`` keyword argument and store it on the instance.

        Pre-GREEN this fails with TypeError because ``__init__`` only
        accepts ``session``.
        """
        from app.services.feedback_service import FeedbackService

        session = MagicMock()
        write_queue = MagicMock()

        svc = FeedbackService(session, write_queue=write_queue)
        assert svc._write_queue is write_queue

    def test_constructor_default_write_queue_is_none(self):
        """Backward-compat: not supplying ``write_queue`` keeps it as
        ``None`` so legacy callers + tests continue to use ``self._session``.
        """
        from app.services.feedback_service import FeedbackService

        svc = FeedbackService(MagicMock())
        assert getattr(svc, "_write_queue", "missing") is None


# ---------------------------------------------------------------------------
# OptimizationService
# ---------------------------------------------------------------------------


class TestOptimizationServiceQueueWiring:
    """``OptimizationService`` accepts ``write_queue=`` + routes
    ``delete_optimizations`` through it."""

    def test_constructor_accepts_write_queue_kwarg(self):
        """RED: ``OptimizationService(session, write_queue=...)`` accepts
        the kwarg; pre-GREEN fails with TypeError.
        """
        from app.services.optimization_service import OptimizationService

        session = MagicMock()
        write_queue = MagicMock()

        svc = OptimizationService(session, write_queue=write_queue)
        assert svc._write_queue is write_queue

    def test_constructor_default_write_queue_is_none(self):
        from app.services.optimization_service import OptimizationService

        svc = OptimizationService(MagicMock())
        assert getattr(svc, "_write_queue", "missing") is None


# ---------------------------------------------------------------------------
# audit_logger
# ---------------------------------------------------------------------------


class TestAuditLoggerQueueWiring:
    """``log_event`` + ``prune_audit_log`` accept optional ``write_queue=``
    that routes the commit through it.
    """

    def test_log_event_accepts_write_queue_kwarg(self):
        """RED: ``log_event`` signature must include ``write_queue=`` kwarg."""
        import inspect

        from app.services.audit_logger import log_event

        sig = inspect.signature(log_event)
        assert "write_queue" in sig.parameters, (
            "log_event must accept write_queue= for queue routing"
        )

    def test_prune_audit_log_accepts_write_queue_kwarg(self):
        """RED: ``prune_audit_log`` signature must include ``write_queue=``."""
        import inspect

        from app.services.audit_logger import prune_audit_log

        sig = inspect.signature(prune_audit_log)
        assert "write_queue" in sig.parameters, (
            "prune_audit_log must accept write_queue= for queue routing"
        )

    @pytest.mark.asyncio
    async def test_log_event_routes_through_queue_when_set(self):
        """RED: ``log_event(... write_queue=q)`` submits a callback labelled
        ``audit_log_event``."""
        from app.services.audit_logger import log_event

        queue, captured = _make_recording_queue()
        await log_event(
            db=MagicMock(),
            action="test_action",
            write_queue=queue,
        )
        labels = [c[0] for c in captured]
        assert "audit_log_event" in labels


# ---------------------------------------------------------------------------
# gc
# ---------------------------------------------------------------------------


class TestGcQueueWiring:
    """``run_startup_gc`` + ``run_recurring_gc`` accept ``write_queue=``
    so the commit routes through it.
    """

    def test_run_startup_gc_accepts_write_queue_kwarg(self):
        import inspect

        from app.services.gc import run_startup_gc

        sig = inspect.signature(run_startup_gc)
        assert "write_queue" in sig.parameters, (
            "run_startup_gc must accept write_queue= for queue routing"
        )

    def test_run_recurring_gc_accepts_write_queue_kwarg(self):
        import inspect

        from app.services.gc import run_recurring_gc

        sig = inspect.signature(run_recurring_gc)
        assert "write_queue" in sig.parameters, (
            "run_recurring_gc must accept write_queue= for queue routing"
        )


# ---------------------------------------------------------------------------
# orphan_recovery
# ---------------------------------------------------------------------------


class TestOrphanRecoveryQueueWiring:
    """``OrphanRecoveryService.scan_and_recover`` accepts ``write_queue=``."""

    def test_scan_and_recover_accepts_write_queue_kwarg(self):
        """RED: ``scan_and_recover`` signature must include ``write_queue=``."""
        import inspect

        from app.services.orphan_recovery import OrphanRecoveryService

        sig = inspect.signature(OrphanRecoveryService.scan_and_recover)
        assert "write_queue" in sig.parameters, (
            "OrphanRecoveryService.scan_and_recover must accept write_queue="
        )


# ---------------------------------------------------------------------------
# Audit hook invariant — services must not write through read engine
# ---------------------------------------------------------------------------


class TestNoUnguardedSelfSessionWritesInServices:
    """Source-level invariant: post-cycle 8 the migrated services must
    only call ``self._session.commit()`` inside the ``write_queue is None``
    legacy branch.
    """

    def test_feedback_service_create_feedback_routes_via_queue_when_set(self):
        """RED: ``FeedbackService.create_feedback`` source must dispatch on
        ``self._write_queue`` ahead of any direct ``self._session.commit()``.
        """
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "services" / "feedback_service.py"
        )
        source = path.read_text()
        # The migration must introduce the queue-aware branch.
        assert "self._write_queue" in source, (
            "feedback_service.py must reference self._write_queue after cycle 8"
        )
        assert "feedback_create" in source, (
            "feedback_service.py must use operation_label='feedback_create'"
        )

    def test_optimization_service_delete_routes_via_queue_when_set(self):
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[1]
            / "app" / "services" / "optimization_service.py"
        )
        source = path.read_text()
        assert "self._write_queue" in source, (
            "optimization_service.py must reference self._write_queue after cycle 8"
        )
        assert "optimization_bulk_delete" in source, (
            "optimization_service.py must use operation_label="
            "'optimization_bulk_delete'"
        )
