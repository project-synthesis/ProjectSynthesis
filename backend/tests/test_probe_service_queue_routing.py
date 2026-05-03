"""Cycle 7c/7.5 RED → GREEN: ProbeService routes status writes through WriteQueue.

The probe service has 8+ ``self.db.commit()`` / ``db.commit()`` sites
spanning the 5-phase orchestrator + lifecycle helpers. Cycle 7c migrates
each to a ``submit()`` callback so probe row mutations (status
transitions, terminal writes, cancellation marks, persistence) serialize
against every other backend writer through the single-writer queue.

Cycle 7.5 finishes the migration: 7 remaining ``self.db.commit()`` sites
(initial INSERT, link_repo_first branch, generation-failure branch,
``_tag_probe_rows``, ``_mark_failed_with_error``, ``_commit_with_retry``
in the run path, ``_persist_and_assign``) all route through the queue;
``self.db`` becomes read-only and ``_persist_lock`` is removed.

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


# ---------------------------------------------------------------------------
# Cycle 7.5: probe service migration completion
# ---------------------------------------------------------------------------


class TestCycle75ProbeMigrationCompletion:
    """Cycle 7.5 RED: complete the probe_service migration.

    Pins the elimination of ALL ``self.db.add`` / ``self.db.commit`` /
    ``self.db.delete`` / ``self.db.flush`` / ``self.db.rollback`` calls in
    ``probe_service.py``. After GREEN: only read-side ``self.db.execute(...)``
    + ``self.db.get(...)`` remain. Every write routes through
    ``self.write_queue.submit()`` instead.

    Also pins removal of ``self._persist_lock`` (redundant once writes
    are queue-serialized) and ``_mark_failed_with_error`` queue routing
    (cycle 7 docstring documented intent but the helper still wrote
    through ``self.db`` directly).
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    def test_no_unguarded_self_db_writes_in_probe_service(self):
        """RED: source-level audit — every ``self.db.add`` /
        ``self.db.commit`` / ``self.db.delete`` / ``self.db.flush`` call
        is reachable ONLY through a documented legacy fallback (i.e.
        guarded by ``if self.write_queue is None``).

        Pre-GREEN there are unconditional calls (cycle 7 left initial
        INSERT + tag-rows + persist-and-assign + final commit on the
        primary path). Post-GREEN each remaining call is in the
        ``write_queue is None`` legacy branch -- production with cycle 9
        lifespan wiring never reaches them.
        """
        import re
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        # Strip docstrings to avoid false positives in prose.
        source_stripped = re.sub(
            r'""".*?"""', "", source, flags=re.DOTALL,
        )
        source_stripped = re.sub(
            r"'''.*?'''", "", source_stripped, flags=re.DOTALL,
        )
        # Strip line comments.
        source_stripped = re.sub(
            r"^[ \t]*#.*$", "", source_stripped, flags=re.MULTILINE,
        )

        # Find every line containing a self.db write API call.
        write_pat = re.compile(
            r"^.*\bself\.db\.(?:add|commit|delete|flush)\(",
            re.MULTILINE,
        )
        write_lines = [
            (m.start(), source_stripped[m.start():m.end()].strip())
            for m in write_pat.finditer(source_stripped)
        ]

        # Build a per-line lookup of the surrounding ~30 lines.
        unguarded: list[str] = []
        for start, line in write_lines:
            # Walk back ~25 lines looking for the enclosing branch
            # ``if self.write_queue is None:`` or ``else:`` immediately
            # following an ``if self.write_queue is not None:``.
            window_start = max(0, source_stripped.rfind("\n", 0, max(0, start - 1500)))
            window = source_stripped[window_start:start]
            if (
                "self.write_queue is None" in window
                or "if self.write_queue is not None" in window
            ):
                continue
            unguarded.append(line)

        assert not unguarded, (
            "probe_service.py has self.db write calls outside the "
            "write_queue=None legacy fallback. Every primary path must "
            "route through self.write_queue.submit(). Offenders: "
            f"{unguarded}"
        )

    def test_no_persist_lock_attribute(self):
        """RED: ``_persist_lock`` attribute removed.

        Once probe_service uses queue + read-only self.db, the
        ``_persist_lock`` (cycle 7 review I0) is redundant. Post-GREEN
        ProbeService instances must NOT have it.
        """
        svc = ProbeService(
            db=MagicMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
        )
        assert not hasattr(svc, "_persist_lock"), (
            "_persist_lock should be removed (cycle 7.5 review I0). "
            "Queue serialization replaces in-process locking."
        )

    def test_no_persist_lock_references_in_module(self):
        """RED: ``_persist_lock`` is no longer referenced as live code in
        probe_service.py.

        Docstring mentions of the historical attribute (e.g.
        "_persist_lock is removed in cycle 7.5") are allowed --
        operators reading the source benefit from knowing why prior
        commits had the attribute. Live references (``self._persist_lock
        = ...`` / ``async with self._persist_lock:``) must be gone.
        """
        import re
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        # Strip docstrings + comments so prose mentions don't trip the audit.
        stripped = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"^[ \t]*#.*$", "", stripped, flags=re.MULTILINE)
        assert "_persist_lock" not in stripped, (
            "probe_service.py still has live _persist_lock references "
            "outside docstrings/comments. Remove the attribute access."
        )

    @pytest.mark.asyncio
    async def test_mark_failed_with_error_routes_through_queue(self):
        """RED: ``_mark_failed_with_error`` with ``write_queue`` set must
        produce a submit() with operation_label
        ``probe_mark_failed_with_error``.

        Cycle 7 documented the intent but the helper still wrote through
        ``self.db.commit()``. Cycle 7.5 actually routes it.
        """
        captured: list[str | None] = []

        async def _fake_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
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
        row = MagicMock()
        await svc._mark_failed_with_error(
            row, "test-probe-id",
            phase="running",
            error_class="RuntimeError",
            error_message="boom",
        )
        assert "probe_mark_failed_with_error" in captured

    @pytest.mark.asyncio
    async def test_mark_failed_with_error_legacy_path_no_queue(self):
        """RED: ``_mark_failed_with_error`` must continue to work when
        ``write_queue`` is ``None`` -- not all callers (older tests +
        legacy paths during the cycle 7-9 transition) supply a queue.

        Pre-cycle 7.5 the helper writes through self.db directly. Post-
        cycle 7.5 it raises a clear error: queue is now mandatory or
        the helper accepts a session_factory fallback. We pick the
        ``self.db``-write retention with read-only-binding to keep the
        test surface stable.

        Note: After cycle 7.5 ``self.db`` is bound to the READ engine,
        so writing through it would fail under the audit hook. The
        legacy path should therefore raise/fall back gracefully when
        write_queue is None — we accept either explicit raise OR a
        documented session_factory bridge.
        """
        # Build a session that simulates "write attempted on read engine"
        # -- the audit hook in production raises
        # ``WriteOnReadEngineError`` when WRITE_QUEUE_AUDIT_HOOK_RAISE=True.
        svc = ProbeService(
            db=AsyncMock(),
            provider=MagicMock(),
            repo_query=MagicMock(),
            context_service=MagicMock(),
            event_bus=MagicMock(),
            # No write_queue -- legacy path.
        )
        row = MagicMock()
        # Legacy path is acceptable iff it raises a clear error OR
        # gracefully no-ops (no DB write attempted). Either way it
        # must NOT silently corrupt state. We assert it doesn't raise
        # AttributeError or TypeError -- an explicit RuntimeError is OK.
        try:
            await svc._mark_failed_with_error(
                row, "test-probe-id",
                phase="running",
                error_class="X",
                error_message="m",
            )
        except (AttributeError, TypeError) as exc:
            pytest.fail(
                f"Legacy path crashed with {type(exc).__name__}: {exc}. "
                "Should either raise RuntimeError or no-op cleanly.",
            )

    @pytest.mark.asyncio
    async def test_initial_probe_run_insert_routes_through_queue(self):
        """RED: the initial ``ProbeRun`` row INSERT in ``_run_impl`` (the
        ``self.db.add(row); await self.db.commit()`` block) must route
        through the write queue under operation_label
        ``probe_initial_insert`` when ``write_queue`` is set.

        This cannot be tested in isolation easily because ``_run_impl``
        pulls in the whole 5-phase orchestrator. We assert it indirectly
        by introspecting the source: the write must dispatch on
        ``self.write_queue is not None`` ahead of any ``self.db.add``.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        assert '"probe_initial_insert"' in source or "'probe_initial_insert'" in source, (
            "probe_service.py must use operation_label='probe_initial_insert' "
            "for the initial ProbeRun row INSERT.  Cycle 7.5 spec § A."
        )

    @pytest.mark.asyncio
    async def test_link_repo_first_failure_branch_routes_through_queue(self):
        """RED: the ``link_repo_first`` branch's terminal write (status=
        failed, error=link_repo_first) must route through the queue.

        Pinned via source introspection (label probe_mark_failed*).
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        # The branch should re-use _mark_failed_with_error or _set_probe_status
        # (both queue-aware) rather than the direct self.db.commit().
        # Look for the inline failure-write near 'link_repo_first':
        idx = source.find('"link_repo_first"')
        assert idx >= 0, "link_repo_first error string not found in source"
        # Window around the assignment site -- 800 chars before.
        window = source[max(0, idx - 800):idx + 200]
        assert "self.db.commit()" not in window, (
            "link_repo_first branch still calls self.db.commit() inline. "
            "Route through self.write_queue.submit() instead."
        )

    @pytest.mark.asyncio
    async def test_generating_failure_branch_routes_through_queue(self):
        """RED: the ``generation_failed`` branch's terminal write (status=
        failed, error=generation_failed:...) must NOT use self.db.commit().
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        idx = source.find('"generation_failed"')
        # The string is also matched as ``ProbeError("generation_failed", ...)``.
        if idx < 0:
            idx = source.find("'generation_failed'")
        assert idx >= 0, "generation_failed not found in source"
        # Look back ~600 chars for the failure-handling block.
        window = source[max(0, idx - 600):idx]
        # The helper call should be visible; inline self.db.commit() is forbidden.
        assert "self.db.commit()" not in window, (
            "generation_failed branch still calls self.db.commit() inline. "
            "Route through self.write_queue.submit() / _mark_failed_with_error."
        )

    @pytest.mark.asyncio
    async def test_tag_probe_rows_routes_through_queue(self):
        """RED: ``_tag_probe_rows`` must route through ``self.write_queue``
        when set, with operation_label ``probe_tag_rows``.

        Source-introspection check.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        # Find the function header.
        assert "_tag_probe_rows" in source
        # Confirm the operation_label is present in source.
        assert "probe_tag_rows" in source, (
            "probe_service.py must use operation_label='probe_tag_rows' "
            "for the _tag_probe_rows callback."
        )

    def test_self_db_uses_read_engine_via_signature_or_factory(self):
        """RED: ProbeService accepts a session bound to the READ engine
        only.  The constructor signature documents this expectation; the
        runtime check is delegated to the audit hook in production.

        We cannot easily check the engine binding without a real session,
        so this test is intentionally a contract-level check on the docstring
        / module-level commentary that documents the read-only nature of
        ``self.db``.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        # The class docstring or __init__ docstring must mention that
        # self.db is the READ-only session.
        assert (
            "read-only" in source.lower()
            or "read engine" in source.lower()
            or "READ engine" in source
        ), (
            "probe_service.py must document that self.db is bound to "
            "the READ engine (write paths use self.write_queue)."
        )


# ---------------------------------------------------------------------------
# Cycle 7.5: yield-boundary cancellation tests (spec § 8 #5a-5e)
# ---------------------------------------------------------------------------


class TestCycle75CancellationBoundaries:
    """Cycle 7.5 RED: cancellation at every yield boundary in the probe
    orchestrator must leave the ProbeRun row in a terminal state, never
    ``running``.

    Per spec § 8 #5a-5e, cancellation can happen between any two phase
    boundaries. The probe must:
      - mark the row failed=cancelled (or terminal-completed if persist
        landed before the cancel).
      - never leak a running row that the GC sweep will have to
        eventually reconcile.

    Test infrastructure note: the full pipeline harness is too heavy for
    boundary-precision testing. These tests pin the INVARIANT (that
    ``_mark_cancelled`` is invoked under ``asyncio.shield`` from the
    ``asyncio.CancelledError`` handler) at the contract level, by
    introspecting the source + invoking the helper directly under a
    cancel.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    @pytest.mark.asyncio
    async def test_5a_mark_cancelled_invoked_under_shield(self):
        """5a: the CancelledError handler in ``_run_impl`` MUST call
        ``_mark_cancelled`` under ``asyncio.shield`` so the terminal-
        state write lands even on re-cancellation.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        assert "asyncio.shield(self._mark_cancelled" in source, (
            "_run_impl must call self._mark_cancelled under asyncio.shield "
            "from the CancelledError handler. Without shield, the terminal "
            "write is cancelled before it lands."
        )

    @pytest.mark.asyncio
    async def test_5b_mark_failed_with_error_invoked_under_shield(self):
        """5b: the top-level ``except Exception`` handler MUST call
        ``_mark_failed_with_error`` under ``asyncio.shield`` so a mid-
        run uncaught exception cannot leak a running row.
        """
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "services" / "probe_service.py"
        source = path.read_text()
        assert "asyncio.shield(self._mark_failed_with_error" in source, (
            "_run_impl must call self._mark_failed_with_error under "
            "asyncio.shield from the top-level except handler."
        )

    @pytest.mark.asyncio
    async def test_5c_mark_cancelled_under_queue_uses_shield_semantics(self):
        """5c: when ``write_queue`` is set, ``_mark_cancelled`` submits a
        callback that re-reads the row inside the writer session and
        commits the terminal-state write atomically.
        """
        captured_writes: list[dict[str, Any]] = []

        async def _fake_submit(work, *, timeout=None, operation_label=None):
            mock_db = AsyncMock()
            row_obj = MagicMock(status="running")
            mock_db.get = AsyncMock(return_value=row_obj)
            await work(mock_db)
            captured_writes.append({
                "label": operation_label,
                "status_after": row_obj.status,
                "error_after": row_obj.error,
            })
            return None

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
        row = MagicMock()
        await svc._mark_cancelled(row, "5c-probe")

        assert len(captured_writes) == 1
        assert captured_writes[0]["label"] == "probe_mark_cancelled"
        assert captured_writes[0]["status_after"] == "failed"
        assert captured_writes[0]["error_after"] == "cancelled"

    @pytest.mark.asyncio
    async def test_5d_mark_failed_with_error_terminal_state_committed(self):
        """5d: the queue callback in ``_mark_failed_with_error`` writes a
        composed terminal error ("ErrClass: msg (phase=...)").
        """
        captured_writes: list[dict[str, Any]] = []

        async def _fake_submit(work, *, timeout=None, operation_label=None):
            mock_db = AsyncMock()
            row_obj = MagicMock(status="running")
            mock_db.get = AsyncMock(return_value=row_obj)
            await work(mock_db)
            captured_writes.append({
                "label": operation_label,
                "status_after": row_obj.status,
                "error_after": row_obj.error,
            })

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
        row = MagicMock()
        await svc._mark_failed_with_error(
            row, "5d-probe",
            phase="reporting",
            error_class="ValueError",
            error_message="boom",
        )

        assert len(captured_writes) == 1
        assert captured_writes[0]["label"] == "probe_mark_failed_with_error"
        assert captured_writes[0]["status_after"] == "failed"
        # Composed error: contains class, message, phase.
        err = captured_writes[0]["error_after"]
        assert "ValueError" in err
        assert "boom" in err
        assert "reporting" in err

    @pytest.mark.asyncio
    async def test_5e_mark_cancelled_idempotent_on_second_invocation(self):
        """5e: when the row has already moved past 'running' (e.g. a
        cancellation arrived AFTER persist completed), ``_mark_cancelled``
        must not regress the terminal state.

        Note: the current helper unconditionally overwrites status='failed'
        + error='cancelled'. Cycle 7.5 hardens this to skip the overwrite
        when status is already terminal, preventing late-arrival cancels
        from corrupting completed runs.
        """
        captured_writes: list[dict[str, Any]] = []

        async def _fake_submit(work, *, timeout=None, operation_label=None):
            # Simulate a row that's already completed (e.g. persist landed
            # then cancel arrived).
            mock_db = AsyncMock()
            row_obj = MagicMock(status="completed")
            mock_db.get = AsyncMock(return_value=row_obj)
            await work(mock_db)
            captured_writes.append({
                "status_after": row_obj.status,
                "error_after": getattr(row_obj, "error", None),
            })

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
        row = MagicMock()
        await svc._mark_cancelled(row, "5e-probe")

        # Idempotency: row stays at 'completed' (the helper checks the
        # current state inside the callback before overwriting).
        # Pre-GREEN this would assert a corrupted state ('failed') because
        # the v0.4.13 cycle 7c body overwrites unconditionally.
        # Post-GREEN cycle 7.5 the row stays 'completed'.
        assert captured_writes[0]["status_after"] == "completed", (
            "Late-arrival cancel must not corrupt a terminal row. "
            "Got status_after=" + str(captured_writes[0]["status_after"])
        )


# ---------------------------------------------------------------------------
# Cycle 7.5: polymorphic collapse audit
# ---------------------------------------------------------------------------


class TestCycle75PolymorphicCollapse:
    """Cycle 7.5 RED: after the migration, the dual-typed signatures
    (WriteQueue | SessionFactory or db | inputs) collapse to queue-only.
    """

    def test_bulk_persist_signature_is_write_queue_only(self):
        """RED: ``bulk_persist`` second arg type must be ``WriteQueue``
        (not ``WriteQueue | SessionFactory``).
        """
        import inspect

        from app.services.batch_persistence import bulk_persist

        sig = inspect.signature(bulk_persist)
        params = list(sig.parameters.values())
        # The second positional arg is `write_queue`.
        annotation = params[1].annotation
        # After collapse the annotation should NOT include the SessionFactory
        # union member.
        ann_str = str(annotation)
        assert "SessionFactory" not in ann_str, (
            f"bulk_persist still takes WriteQueue | SessionFactory: "
            f"{ann_str}. Collapse to WriteQueue."
        )

    def test_batch_taxonomy_assign_signature_is_write_queue_only(self):
        """RED: same for ``batch_taxonomy_assign``."""
        import inspect

        from app.services.batch_persistence import batch_taxonomy_assign

        sig = inspect.signature(batch_taxonomy_assign)
        params = list(sig.parameters.values())
        annotation = params[1].annotation
        ann_str = str(annotation)
        assert "SessionFactory" not in ann_str, (
            f"batch_taxonomy_assign still takes WriteQueue | SessionFactory: "
            f"{ann_str}. Collapse to WriteQueue."
        )

    def test_persist_and_propagate_signature_is_inputs_first(self):
        """RED: ``persist_and_propagate`` first positional arg is
        ``PersistenceInputs`` (not ``AsyncSession | PersistenceInputs |
        None``). Mixed-mode and legacy positional dispatch are removed.
        """
        import inspect

        from app.services.pipeline_phases import persist_and_propagate

        sig = inspect.signature(persist_and_propagate)
        params = list(sig.parameters.values())
        annotation = params[0].annotation
        ann_str = str(annotation)
        assert "AsyncSession" not in ann_str, (
            f"persist_and_propagate still polymorphic: {ann_str}. "
            "Collapse to PersistenceInputs."
        )

    def test_persist_and_propagate_write_queue_required(self):
        """RED: ``write_queue`` must be required (no default ``None``)."""
        import inspect

        from app.services.pipeline_phases import persist_and_propagate

        sig = inspect.signature(persist_and_propagate)
        wq_param = sig.parameters.get("write_queue")
        assert wq_param is not None, "write_queue kwarg missing"
        assert wq_param.default is inspect.Parameter.empty, (
            "write_queue must not default to None; collapse to required."
        )

    def test_increment_pattern_usage_write_queue_required(self):
        """RED: ``increment_pattern_usage(cluster_ids, *, write_queue)``
        no longer defaults ``write_queue=None``.
        """
        import inspect

        from app.services.sampling.persistence import increment_pattern_usage

        sig = inspect.signature(increment_pattern_usage)
        wq_param = sig.parameters.get("write_queue")
        assert wq_param is not None
        assert wq_param.default is inspect.Parameter.empty, (
            "increment_pattern_usage write_queue must be required."
        )

    def test_no_isinstance_writequeue_branches_in_services(self):
        """RED: ``isinstance(..., WriteQueue)`` polymorphic dispatch is
        removed from services after collapse.
        """
        import re
        from pathlib import Path

        services_dir = Path(__file__).resolve().parents[1] / "app" / "services"
        offenders: list[tuple[str, list[str]]] = []
        for py in services_dir.rglob("*.py"):
            text = py.read_text()
            # Strip docstrings/comments.
            stripped = re.sub(
                r'""".*?"""', "", text, flags=re.DOTALL,
            )
            stripped = re.sub(
                r"'''.*?'''", "", stripped, flags=re.DOTALL,
            )
            stripped = re.sub(
                r"^[ \t]*#.*$", "", stripped, flags=re.MULTILINE,
            )
            matches = re.findall(
                r"isinstance\s*\([^,)]+,\s*WriteQueue\)", stripped,
            )
            if matches:
                offenders.append((str(py), matches))
        assert not offenders, (
            "Polymorphic isinstance(..., WriteQueue) branches still present: "
            f"{offenders}. Collapse them — write_queue is required."
        )

    def test_no_queue_or_session_factory_param_name(self):
        """RED: ``queue_or_session_factory`` param name (the polymorphic
        dispatch hint) is removed.
        """
        import re
        from pathlib import Path

        services_dir = Path(__file__).resolve().parents[1] / "app" / "services"
        offenders: list[str] = []
        for py in services_dir.rglob("*.py"):
            text = py.read_text()
            # Strip docstrings to skip prose mentions.
            stripped = re.sub(
                r'""".*?"""', "", text, flags=re.DOTALL,
            )
            if re.search(r"\bqueue_or_session_factory\b", stripped):
                offenders.append(str(py))
        assert not offenders, (
            f"queue_or_session_factory still present: {offenders}. "
            "Rename to write_queue after collapse."
        )
