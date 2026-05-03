"""Tests for ``app.services.sampling.persistence`` write-queue migration.

v0.4.13 cycle 5 — ``increment_pattern_usage`` is the sole commit site under
``backend/app/services/sampling/persistence.py`` (line 88, ``await db.commit()``
inside the ``async_session_factory()`` block). Per spec § 3.4 + § 4.1 cycle 5
row, this commit must route through the single-writer ``WriteQueue`` when
the caller threads one through.

Acceptance per cycle 5:

* ``write_queue`` keyword arg accepted on the function signature with
  Option C dual-typed default ``None``.
* When supplied, the per-cluster ``increment_usage`` loop + the trailing
  ``await db.commit()`` collapse into ONE ``submit()`` call carrying
  ``operation_label='sampling_persist'``.
* Failure semantics: ``WriteQueue*Error`` propagates to the caller; the
  surrounding ``except Exception`` is preserved as a non-fatal warn-log
  parallel to v0.4.12 (matches the ``logger.warning(...)`` suppression on
  unexpected errors so a single bad cluster_id does not abort the pipeline).

Until cycle 7 retires the legacy path, ``write_queue=None`` keeps the
``async_session_factory()`` form live so the still-unmigrated
``sampling_pipeline.py`` orchestrator caller compiles unchanged.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio

import pytest


class TestSamplingPersistViaQueue:
    """Cycle 5 RED → GREEN: ``increment_pattern_usage`` routes through the
    single-writer ``WriteQueue`` instead of opening a fresh
    ``async_session_factory()`` session.

    Mirrors cycle 2 (``bulk_persist``) + cycle 3 (``batch_taxonomy_assign``)
    + cycle 4 (``persist_and_propagate``) Option C dual-typed signatures.
    """

    @pytest.mark.asyncio
    async def test_sampling_persist_routes_through_queue(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: passing ``write_queue=`` to ``increment_pattern_usage`` must
        produce ONE ``submit()`` call labelled ``sampling_persist``.

        FAILS pre-GREEN with ``TypeError: increment_pattern_usage() got an
        unexpected keyword argument 'write_queue'`` -- the v0.4.12 signature
        is ``(cluster_ids: set[str])`` only.

        After GREEN, the queue captures exactly one submit; legacy path
        (when ``write_queue=None``) continues to operate on
        ``async_session_factory()`` directly.
        """
        from app.services.sampling import persistence as sp
        from tests._write_queue_helpers import create_prestaged_cluster

        # Pre-stage one PromptCluster row so the increment_usage loop has
        # something real to mutate -- mirrors cycle 4's prestaged cluster
        # pattern so the queue callback exercises real ORM writes, not a
        # dry-run no-op.
        cid = await create_prestaged_cluster(
            write_queue_inmem._writer_engine,
            label="cycle5-test-cluster",
        )

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)

        await sp.increment_pattern_usage(  # type: ignore[call-arg]
            {cid}, write_queue=write_queue_inmem,
        )

        assert "sampling_persist" in captured, (
            "expected one submit() with operation_label='sampling_persist'; "
            f"got {captured!r}"
        )
        # Pin: exactly one submit (the per-cluster loop + commit collapse
        # into 1). If a future refactor splits this into per-cluster
        # submits, this assertion fires and forces a spec re-read.
        assert captured.count("sampling_persist") == 1, (
            "increment_pattern_usage commit site must collapse into ONE submit; "
            f"got {captured.count('sampling_persist')} submits"
        )

    @pytest.mark.asyncio
    async def test_sampling_persist_does_not_open_async_session_factory(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: the queue callback must NOT open ``async_session_factory()``.
        The v0.4.12 code opened a fresh session via
        ``async with async_session_factory() as db:`` (line 76); post-GREEN
        that block is gone -- the single writer session inside the queue
        callback does the loop AND the commit.

        Failure mode pre-GREEN: ``increment_pattern_usage`` rejects
        ``write_queue=`` (TypeError). Same RED signal as the sibling test.

        Failure mode if a future refactor re-introduces the legacy session:
        the ``async_session_factory`` spy fires and the test fails LOUDLY
        with the offending call site in the traceback.
        """
        from app.services.sampling import persistence as sp
        from tests._write_queue_helpers import create_prestaged_cluster

        cid = await create_prestaged_cluster(
            write_queue_inmem._writer_engine,
            label="cycle5-spy-cluster",
        )

        factory_calls: list[str] = []

        def _spy(*args, **kwargs):
            factory_calls.append("called")
            from app.database import async_session_factory as _real
            return _real(*args, **kwargs)

        monkeypatch.setattr("app.database.async_session_factory", _spy)
        # Patch the local symbol too — ``persistence.py`` does
        # ``from app.database import async_session_factory`` at module
        # scope, so monkeypatching the source attribute alone leaves the
        # already-bound local reference intact.
        monkeypatch.setattr(
            "app.services.sampling.persistence.async_session_factory", _spy,
        )

        await sp.increment_pattern_usage(  # type: ignore[call-arg]
            {cid}, write_queue=write_queue_inmem,
        )

        assert factory_calls == [], (
            "post-GREEN increment_pattern_usage must NOT call "
            "async_session_factory() inside the queue callback; "
            f"got {len(factory_calls)} call(s)"
        )
