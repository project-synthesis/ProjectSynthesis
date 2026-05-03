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

        await sp.increment_pattern_usage(
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

        await sp.increment_pattern_usage(
            {cid}, write_queue=write_queue_inmem,
        )

        assert factory_calls == [], (
            "post-GREEN increment_pattern_usage must NOT call "
            "async_session_factory() inside the queue callback; "
            f"got {len(factory_calls)} call(s)"
        )


# ---------------------------------------------------------------------------
# Cycle 5 OPERATE: dynamic concurrency + per-cluster isolation under realistic
#                  load
# ---------------------------------------------------------------------------
#
# Per ``feedback_tdd_protocol.md`` Phase 5, dynamic verification under
# realistic concurrent load — proves the migrated ``increment_pattern_usage``
# actually delivers on the queue's promises (no ``database is locked`` under
# N=5 contention) AND pins three invariants:
#
# * Test #1 — N=5 concurrent QUEUE callers: queue serialization eliminates
#   writer contention; all 5 callers' usage_count bumps land in the DB; zero
#   'database is locked' log records; queue depth bounded.
# * Test #2 — usage_count visible after submit() resolves: pins the cycle 5
#   contract that the queue callback's commit makes the row visible to
#   subsequent reads (mirrors cycle 4 'event emission AFTER queue resolves'
#   ordering, adapted for cycle 5's no-event post-commit telemetry path).
# * Test #3 — per-cluster failure isolation: a single bad cluster_id (engine
#   raises) falls back to direct UPDATE inside the same session, so the
#   remaining cluster_ids in the set still get their bumps. Pins the v0.4.12
#   invariant that a single bad cluster_id does NOT poison the whole batch.
# ---------------------------------------------------------------------------


class TestSamplingPersistOperate:
    """OPERATE phase: mirrors cycle 2/3/4 ``Test*Operate`` structure for
    cycle 5 ``increment_pattern_usage``.

    Test #1 uses ``writer_engine_file`` for real WAL contention. Tests
    #2-3 use the in-memory queue fixture (logic-only, no contention
    required).

    The class-level ``reset_taxonomy_engine`` fixture (promoted to
    ``conftest.py``) ensures every test starts with a fresh
    ``TaxonomyEngine`` singleton so accumulated state from prior tests
    doesn't bleed into assert paths. Cycle 5 ``increment_pattern_usage``
    reads ``get_engine()`` for usage propagation; reusing the singleton
    across tests makes engine state path-dependent on test ordering.
    """

    pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")

    # -- Test #1: N=5 concurrent QUEUE callers, real WAL contention ---------

    @pytest.mark.asyncio
    async def test_sampling_persist_n5_concurrent_via_queue(
        self, writer_engine_file, caplog,
    ):
        """N=5 concurrent ``increment_pattern_usage`` callers, each on a
        distinct PromptCluster, routing through the ``WriteQueue``.

        The queue's serialization is the only defense against SQLite
        writer contention — without it, file-mode WAL with concurrent
        writers surfaces 'database is locked' in SQLAlchemy ERROR-level
        logs. The per-caller commit collapses into ONE ``submit()``.

        Asserts:
        - All 5 PromptCluster.usage_count rows == 1 in DB.
        - Zero 'database is locked' log records.
        - Queue depth never exceeds ``max_depth`` during the run.
        - Wall-clock budget < 30s.
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time

        from sqlalchemy import text as _sa_text

        from app.models import Base
        from app.services.sampling import persistence as sp
        from app.services.write_queue import WriteQueue
        from tests._write_queue_helpers import create_prestaged_cluster

        # Materialize schema on the file-mode engine (writer_engine_file
        # does NOT auto-create tables, only writer_engine_inmem does).
        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Pre-stage 5 distinct PromptCluster rows on the writer engine —
        # each caller mutates its own row so we can verify per-caller
        # isolation after the gather().
        cluster_ids = [
            await create_prestaged_cluster(
                writer_engine_file, label=f"c5-op-cluster-{i}",
            )
            for i in range(5)
        ]

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        # Track queue depth across callers — a sentinel sample loop.
        observed_depths: list[int] = []
        depth_sampler_done = _asyncio.Event()

        async def _sample_depth() -> None:
            while not depth_sampler_done.is_set():
                observed_depths.append(queue.queue_depth)
                try:
                    await _asyncio.wait_for(
                        depth_sampler_done.wait(), timeout=0.05,
                    )
                except _asyncio.TimeoutError:
                    pass

        sampler_task = _asyncio.create_task(_sample_depth())

        try:
            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                await _asyncio.gather(*[
                    sp.increment_pattern_usage({cid}, write_queue=queue)
                    for cid in cluster_ids
                ])
            elapsed = _time.monotonic() - t0
            depth_sampler_done.set()
            await sampler_task

            # O1: SELECT to verify user-visible state — usage_count == 1
            # on every cluster (each caller bumped exactly its own row).
            ids_param = ",".join(f"'{cid}'" for cid in cluster_ids)
            async with writer_engine_file.connect() as conn:
                rows_q = await conn.execute(_sa_text(
                    f"SELECT id, usage_count FROM prompt_cluster "  # noqa: S608
                    f"WHERE id IN ({ids_param})"
                ))
                usage_by_id = {r[0]: int(r[1]) for r in rows_q.fetchall()}
            assert len(usage_by_id) == 5, (
                f"expected 5 PromptCluster rows, got {len(usage_by_id)}"
            )
            for cid, count in usage_by_id.items():
                assert count == 1, (
                    f"cluster {cid} usage_count={count}, expected 1"
                )

            # O2: zero 'database is locked' anywhere in caplog.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under N=5 queue concurrency: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # Queue depth bounded — never exceeded our max_depth (64).
            max_seen_depth = max(observed_depths) if observed_depths else 0
            assert max_seen_depth <= 64, (
                f"queue depth peaked at {max_seen_depth}, exceeded cap"
            )

            # Wall-clock budget: 5 increments under N=5 concurrency comfortably <30s.
            assert elapsed < 30.0, (
                f"queue stress run took {elapsed:.1f}s, > 30s budget"
            )
        finally:
            depth_sampler_done.set()
            if not sampler_task.done():
                await sampler_task
            await queue.stop(drain_timeout=5.0)

    # -- Test #2: usage_count visible after submit() resolves ---------------

    @pytest.mark.asyncio
    async def test_sampling_persist_usage_visible_after_queue_resolves(
        self, write_queue_inmem,
    ):
        """The cycle 5 contract: when ``submit()`` returns, the
        per-cluster ``usage_count`` bump is durably visible to subsequent
        reads on the same engine. Mirrors cycle 4 'event emission AFTER
        queue resolves' ordering, adapted for cycle 5's no-event
        post-commit telemetry path.

        Pre-fix risk: if a future refactor moved the commit OUTSIDE the
        ``_do_increment`` callback (e.g. external transaction wrapper),
        the post-submit SELECT could read pre-bump usage_count=0. Pins
        durable visibility.
        """
        from sqlalchemy import text as _sa_text

        from app.services.sampling import persistence as sp
        from tests._write_queue_helpers import create_prestaged_cluster

        cid = await create_prestaged_cluster(
            write_queue_inmem._writer_engine,
            label="c5-op-visible",
        )

        # Sanity: pre-bump usage_count == 0 (default on new cluster row).
        async with write_queue_inmem._writer_engine.connect() as conn:
            pre = await conn.execute(_sa_text(
                "SELECT usage_count FROM prompt_cluster WHERE id = :cid"
            ), {"cid": cid})
            pre_row = pre.first()
            assert pre_row is not None
            assert int(pre_row[0]) == 0, (
                f"pre-bump usage_count expected 0, got {pre_row[0]}"
            )

        # Submit + await resolution.
        await sp.increment_pattern_usage({cid}, write_queue=write_queue_inmem)

        # Post-bump usage_count must be 1 on the SAME engine.
        async with write_queue_inmem._writer_engine.connect() as conn:
            post = await conn.execute(_sa_text(
                "SELECT usage_count FROM prompt_cluster WHERE id = :cid"
            ), {"cid": cid})
            post_row = post.first()
            assert post_row is not None
            assert int(post_row[0]) == 1, (
                f"post-bump usage_count expected 1, got {post_row[0]} -- "
                f"the queue callback's commit() did not durably persist"
            )

    # -- Test #3: per-cluster failure isolated, batch survives --------------

    @pytest.mark.asyncio
    async def test_sampling_persist_failure_isolated_per_cluster(
        self, write_queue_inmem, monkeypatch,
    ):
        """A single failing ``engine.increment_usage(cid)`` call must NOT
        poison the rest of the cluster_ids in the set. Pins the v0.4.12
        per-cluster try/except + UPDATE-fallback invariant under cycle 5
        queue routing.

        Setup: monkey-patch ``engine.increment_usage`` to raise on the
        first cluster_id but succeed on the rest. Verify all clusters
        end with usage_count == 1 (the failing cluster_id falls back to
        the direct UPDATE inside the same session).
        """
        from sqlalchemy import text as _sa_text

        from app.services.sampling import persistence as sp
        from app.services.taxonomy import get_engine
        from tests._write_queue_helpers import create_prestaged_cluster

        # Pre-stage 3 clusters.
        cluster_ids = [
            await create_prestaged_cluster(
                write_queue_inmem._writer_engine, label=f"c5-op-iso-{i}",
            )
            for i in range(3)
        ]
        # Sort so the first-iterated cluster is deterministic. Sets
        # iterate by hash but for str of len 36 (uuid4) Python's hash is
        # randomized per-process — so we can't pin which cluster_id
        # iterates first. We'll spy on whichever ID hits increment_usage
        # first and raise once.
        engine = get_engine()
        original_increment = engine.increment_usage
        first_cluster_seen: list[str] = []

        async def _flaky_increment(cluster_id: str, db) -> None:
            if not first_cluster_seen:
                first_cluster_seen.append(cluster_id)
                raise RuntimeError(
                    "synthetic engine failure for cycle 5 isolation test"
                )
            await original_increment(cluster_id, db)

        monkeypatch.setattr(engine, "increment_usage", _flaky_increment)

        await sp.increment_pattern_usage(
            set(cluster_ids), write_queue=write_queue_inmem,
        )

        # All 3 clusters should have usage_count == 1 — the failing one
        # via the UPDATE fallback, the rest via engine.increment_usage.
        ids_param = ",".join(f"'{cid}'" for cid in cluster_ids)
        async with write_queue_inmem._writer_engine.connect() as conn:
            rows_q = await conn.execute(_sa_text(
                f"SELECT id, usage_count FROM prompt_cluster "  # noqa: S608
                f"WHERE id IN ({ids_param})"
            ))
            usage_by_id = {r[0]: int(r[1]) for r in rows_q.fetchall()}

        assert len(usage_by_id) == 3, (
            f"expected 3 PromptCluster rows, got {len(usage_by_id)}"
        )
        # The failing cluster_id's row was bumped via direct UPDATE
        # fallback. The other two via engine.increment_usage. All 3 == 1.
        for cid, count in usage_by_id.items():
            assert count == 1, (
                f"cluster {cid} usage_count={count}, expected 1 "
                f"(direct-UPDATE fallback OR engine.increment_usage)"
            )
        # Sanity: engine spy DID see one failure-triggering cluster.
        assert len(first_cluster_seen) == 1, (
            f"expected exactly 1 cluster to trigger the synthetic "
            f"engine failure, got {len(first_cluster_seen)}"
        )
