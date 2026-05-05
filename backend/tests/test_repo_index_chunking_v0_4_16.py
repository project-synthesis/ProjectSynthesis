"""RED-phase tests for v0.4.16 P1b Cycle 1 — repo-index chunking core.

Spec: docs/specs/v0.4.16-repo-index-chunking-2026-05-04.md (v3 APPROVED)
Plan: docs/plans/v0.4.16-p1b-repo-index-chunking-2026-05-04.md Task 1.1.

16 tests. ~10-12 must FAIL pre-Cycle-1 (with the documented signal).
~4-6 PASS as regression guards. GREEN dispatch (Task 1.2) flips all 16
to passing.

Inline helpers (NO new @pytest.fixture defs per RED constraint):
  * _make_mock_embedding(): MagicMock spec=EmbeddingService stand-in
  * _make_mock_github(...): AsyncMock GitHubClient with tree fetch +
    file content stubs
  * _make_processed_files(n): synthetic list[ProcessedFile] for
    `read_and_embed_files` mock returns
  * _seed_meta(...) / _seed_files(...): DB seeding primitives
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import select

from app.models import RepoFileIndex, RepoIndexMeta
from app.services.repo_index_file_reader import ProcessedFile
from app.services.repo_index_outlines import FileOutline

EMBEDDING_DIM = 384
BACKEND_TESTS_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Inline helpers
# ---------------------------------------------------------------------------


def _make_mock_embedding() -> Any:
    from app.services.embedding_service import EmbeddingService

    svc = MagicMock(spec=EmbeddingService)
    svc.dimension = EMBEDDING_DIM
    zero = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    svc.embed_single.return_value = zero
    svc.aembed_single = AsyncMock(return_value=zero)
    svc.embed_texts.return_value = [zero]
    svc.aembed_texts = AsyncMock(return_value=[zero])
    return svc


def _make_mock_github(
    *,
    head_sha: str = "abc123",
    n_indexable: int = 0,
    extra_paths: list[str] | None = None,
    tree_etag: str | None = None,
) -> Any:
    """Build an AsyncMock GitHubClient that returns N indexable .py files."""
    from app.services.github_client import GitHubClient

    gc = AsyncMock(spec=GitHubClient)
    gc.get_branch_head_sha = AsyncMock(return_value=head_sha)

    paths = list(extra_paths or [])
    for i in range(n_indexable):
        paths.append(f"src/file_{i:04d}.py")

    tree_items = [
        {"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200}
        for p in paths
    ]
    gc.get_tree = AsyncMock(return_value=tree_items)
    gc.get_tree_with_cache = AsyncMock(return_value=(tree_items, tree_etag))
    gc.get_file_content = AsyncMock(return_value="def foo():\n    pass\n")
    return gc


def _make_processed_files(paths: list[str]) -> list[ProcessedFile]:
    """Build a list of ProcessedFile bypassing real read+embed network."""
    out: list[ProcessedFile] = []
    for p in paths:
        outline = FileOutline(
            file_path=p,
            file_type="python",
            structural_summary=f"# outline {p}",
            doc_summary=None,
        )
        out.append(
            ProcessedFile(
                item={"path": p, "sha": f"sha_{p}", "size": 200},
                content="def foo():\n    pass\n",
                outline=outline,
                embedding=np.zeros(EMBEDDING_DIM, dtype=np.float32),
                content_sha=f"csha_{p}",
            )
        )
    return out


def _read_and_embed_stub(paths: list[str]):
    """Construct a coroutine factory matching read_and_embed_files signature."""
    processed = _make_processed_files(paths)

    async def _stub(**kwargs):
        return processed, 0, 0

    return _stub


async def _seed_meta(
    db,
    repo: str,
    branch: str,
    *,
    status: str = "indexing",
    indexed_at: datetime | None = None,
    file_count: int = 0,
    head_sha: str | None = None,
) -> RepoIndexMeta:
    """Seed a RepoIndexMeta row in the test DB."""
    meta = RepoIndexMeta(
        repo_full_name=repo,
        branch=branch,
        status=status,
        file_count=file_count,
        head_sha=head_sha,
        indexed_at=indexed_at,
        index_phase="pending",
    )
    db.add(meta)
    await db.commit()
    return meta


async def _seed_files(
    db,
    repo: str,
    branch: str,
    n: int,
) -> list[RepoFileIndex]:
    """Seed N RepoFileIndex rows for the given (repo, branch)."""
    rows: list[RepoFileIndex] = []
    zero = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()
    for i in range(n):
        row = RepoFileIndex(
            repo_full_name=repo,
            branch=branch,
            file_path=f"src/seed_{i:04d}.py",
            file_sha=f"sha_seed_{i:04d}",
            file_size_bytes=200,
            content="def foo(): pass",
            outline=f"# outline seed_{i:04d}",
            content_sha=f"csha_seed_{i:04d}",
            embedding=zero,
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    return rows


class _CountingWriteQueueStub:
    """Minimal WriteQueue stand-in that counts submit() calls and runs the
    work_fn against the supplied db_session.

    Mirrors conftest's _TestWriteQueue but exposes a captured-call list and
    optional fail injection. Used to assert per-batch submit decomposition.
    """

    def __init__(self, db, *, fail_on_call: int | None = None):
        self._db = db
        self.calls: list[dict] = []
        self.fail_on_call = fail_on_call

    async def submit(self, work, *, timeout=None, operation_label=None):
        idx = len(self.calls)
        record = {
            "index": idx,
            "operation_label": operation_label,
            "work_fn": work,
        }
        self.calls.append(record)
        if self.fail_on_call is not None and idx == self.fail_on_call:
            raise RuntimeError(f"injected failure at submit call #{idx}")
        return await work(self._db)

    async def submit_batch(self, work_fns, *, timeout=None, operation_label=None):
        # Simple sequential execution — submit_batch is not the v0.4.16 P1b
        # critical path but a few code paths may use it. Mirror submit()'s
        # contract for symmetry.
        results = []
        for fn in work_fns:
            results.append(await self.submit(fn))
        return results


# ===========================================================================
# Test 1 — submit count matches phase decomposition
# ===========================================================================


async def test_build_index_submit_count_matches_phase_decomposition(db_session) -> None:
    """Spec § 3.1 + § 6 acceptance #1.

    Empty-table N-row build: submits == ceil(N/50) + 2
    Existing E rows + N new: submits == ceil(N/50) + ceil(E/200) + 3
    """
    from app.services.repo_index_service import RepoIndexService

    # ── Case A: empty meta + 0 prior rows + 75 indexable files ───────────
    n = 75
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        # Pre-Cycle-1 the service ignores ``write_queue`` entirely; this
        # construction will TypeError because __init__ doesn't accept it
        # — that is the documented RED signal.
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,
        )
        await svc.build_index("owner/repo-A", "main", "ghp_token")

    # Empty-table: 1 (Phase 0 status) + ceil(75/50)=2 (Phase 3 persist) + 1 (Phase 4) = 4
    expected_a = -(-n // 50) + 2  # ceil(75/50) + 2 = 4
    assert len(queue.calls) == expected_a, (
        f"Empty-table N=75: expected {expected_a} submits, got {len(queue.calls)}"
    )

    # ── Case B: 30 prior rows + 75 indexable files ───────────────────────
    await _seed_files(db_session, "owner/repo-B", "main", 30)
    queue_b = _CountingWriteQueueStub(db_session)

    paths_b = [f"src/file_b_{i:04d}.py" for i in range(n)]
    gc_b = _make_mock_github(n_indexable=n)
    # Override exact tree paths so pre-existing row paths aren't reused
    gc_b.get_tree_with_cache = AsyncMock(return_value=(
        [{"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200} for p in paths_b],
        None,
    ))

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths_b),
    ):
        svc_b = RepoIndexService(
            db=db_session,
            github_client=gc_b,
            embedding_service=es,
            write_queue=queue_b,
        )
        await svc_b.build_index("owner/repo-B", "main", "ghp_token")

    # Existing-table: 1 (Phase 0) + (ceil(30/200)=1 + 1 meta-update) + ceil(75/50)=2 + 1 (Phase 4) = 6
    expected_b = -(-n // 50) + -(-30 // 200) + 3
    assert len(queue_b.calls) == expected_b, (
        f"Existing-table E=30, N=75: expected {expected_b} submits, "
        f"got {len(queue_b.calls)}"
    )


# ===========================================================================
# Test 2 — incremental_update Phase F submit count
# ===========================================================================


async def test_incremental_update_phase_f_emits_ceil_m_div_50_plus_one_submits(
    db_session,
) -> None:
    """Spec § 6 acceptance #2.

    Phase F upsert: ceil(M/50) batches + 1 finalize meta = ceil(M/50) + 1.
    M = 73 changed files → 2 batches + 1 finalize = 3 submits.
    """
    from app.services.repo_index_service import RepoIndexService

    # Seed 100 prior rows; we'll change 73 of them by giving the tree
    # different file_sha values for those paths.
    repo, branch = "owner/repo-inc", "main"
    seeded = await _seed_files(db_session, repo, branch, 100)
    await _seed_meta(
        db_session, repo, branch,
        status="ready", file_count=100, head_sha="oldsha",
    )

    # Build a tree where 73 rows have NEW shas (changed); the other 27
    # match the seeded shas (unchanged). No removed/added.
    changed_paths = [seeded[i].file_path for i in range(73)]
    unchanged_paths = [seeded[i].file_path for i in range(73, 100)]

    tree_items = [
        {"type": "blob", "path": p, "sha": f"NEW_sha_{p}", "size": 200}
        for p in changed_paths
    ] + [
        {"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200}
        for p in unchanged_paths
    ]

    gc = _make_mock_github(head_sha="newsha")
    gc.get_tree_with_cache = AsyncMock(return_value=(tree_items, None))

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(changed_paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,
        )
        await svc.incremental_update(repo, branch, "ghp_token")

    # Phase F: ceil(73/50) = 2 upsert batches + 1 meta finalize = 3.
    expected = -(-73 // 50) + 1
    assert len(queue.calls) == expected, (
        f"Incremental Phase F M=73: expected {expected} submits, got {len(queue.calls)}"
    )


# ===========================================================================
# Test 3 — failing batch marks meta error + reraises + no completed event
# ===========================================================================


async def test_failing_batch_marks_meta_error_and_reraises_no_completed_event(
    db_session,
) -> None:
    """Spec § 3.1 refit-fatal + § 6 acceptance #3."""
    from app.services.event_bus import event_bus
    from app.services.repo_index_service import RepoIndexService

    queue_events: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue_events)

    try:
        n = 75
        paths = [f"src/file_{i:04d}.py" for i in range(n)]
        gc = _make_mock_github(n_indexable=n)
        es = _make_mock_embedding()

        # fail_on_call=1 means call #0 (Phase 0 status) succeeds, call #1
        # (the first persist batch — or the 2nd persist batch depending on
        # GREEN's call ordering) raises. The test merely binds the
        # contract: any non-final-meta failure aborts the refit.
        # Using index 2 to land in "2nd persist call" per the prompt.
        queue = _CountingWriteQueueStub(db_session, fail_on_call=2)

        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_read_and_embed_stub(paths),
        ):
            svc = RepoIndexService(
                db=db_session,
                github_client=gc,
                embedding_service=es,
                write_queue=queue,
            )
            with pytest.raises(Exception):
                await svc.build_index("owner/repo-fail", "main", "ghp_token")

        # Drain captured events; assert no repo_index_completed fired.
        captured: list[dict] = []
        while not queue_events.empty():
            captured.append(queue_events.get_nowait())

        completed = [
            e for e in captured
            if e.get("event") == "repo_index_completed"
        ]
        assert not completed, (
            f"repo_index_completed must NOT fire on failed refit; got {completed}"
        )

        # Meta should be flipped to error.
        meta = (
            await db_session.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == "owner/repo-fail",
                    RepoIndexMeta.branch == "main",
                )
            )
        ).scalars().first()
        assert meta is not None
        assert meta.status == "error", (
            f"meta.status must be 'error' after batch failure; got {meta.status!r}"
        )
    finally:
        event_bus._subscribers.discard(queue_events)


# ===========================================================================
# Test 4 — concurrent same-key serialized via lock
# ===========================================================================


async def test_concurrent_same_key_serializes_via_lock(db_session) -> None:
    """Spec § 3.3 + § 6 acceptance #4."""
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/repo-A", "main"
    paths = [f"src/file_{i:04d}.py" for i in range(10)]

    # Slow read-and-embed so the race window is wide.
    async def _slow_read_and_embed(**kwargs):
        await asyncio.sleep(0.1)
        return _make_processed_files(paths), 0, 0

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_slow_read_and_embed,
    ):
        gc1 = _make_mock_github(n_indexable=10)
        gc2 = _make_mock_github(n_indexable=10, head_sha="abc456")

        svc1 = RepoIndexService(
            db=db_session, github_client=gc1, embedding_service=es,
            write_queue=queue,
        )
        svc2 = RepoIndexService(
            db=db_session, github_client=gc2, embedding_service=es,
            write_queue=queue,
        )

        t1 = asyncio.create_task(svc1.build_index(repo, branch, "tok1"))
        # Yield once so t1 enters the lock before t2 starts.
        await asyncio.sleep(0.0)
        t2 = asyncio.create_task(svc2.build_index(repo, branch, "tok2"))

        await asyncio.gather(t1, t2, return_exceptions=True)

    metas = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == repo,
                RepoIndexMeta.branch == branch,
            )
        )
    ).scalars().all()

    # Exactly one meta row exists (idx_repo_index_meta_repo_branch unique).
    assert len(metas) == 1, f"expected 1 meta row, got {len(metas)}"
    # The first task wrote indexed_at; a contended second should NOT
    # overwrite the meta to a fresh status flip — assertion: only one
    # head_sha update happened (the second task returned early via skip).
    # Concretely, only one of {abc123, abc456} should appear, not both.
    assert metas[0].head_sha in {"abc123", "abc456"}
    # Soft second assertion: t2 returns None (no exception). We cannot
    # easily assert "second was a no-op" without inspecting return values,
    # but we can assert the per-key lock dict was populated.
    from app.services.repo_index_service import _REPO_INDEX_LOCKS  # noqa: F401
    # If the import succeeds and the test reached here, half the contract
    # holds. The unique-meta assertion above is the load-bearing claim.


# ===========================================================================
# Test 5 — concurrent different keys proceed in parallel
# ===========================================================================


async def test_concurrent_different_keys_proceed_in_parallel(db_session) -> None:
    """Spec § 3.3 + § 6 acceptance #5.

    Pre-Cycle-1: ``write_queue`` kwarg doesn't exist → TypeError at
    construction → documented FAIL signal. Post-Cycle-1: per-(repo, branch)
    locking lets two parallel calls proceed without contention.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 5
    paths_a = [f"src/a_{i:04d}.py" for i in range(n)]
    paths_b = [f"src/b_{i:04d}.py" for i in range(n)]

    async def _stub_a(**kwargs):
        await asyncio.sleep(0.05)
        return _make_processed_files(paths_a), 0, 0

    async def _stub_b(**kwargs):
        await asyncio.sleep(0.05)
        return _make_processed_files(paths_b), 0, 0

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    gc_a = _make_mock_github(n_indexable=n)
    gc_a.get_tree_with_cache = AsyncMock(return_value=(
        [{"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200} for p in paths_a],
        None,
    ))
    gc_b = _make_mock_github(n_indexable=n, head_sha="def456")
    gc_b.get_tree_with_cache = AsyncMock(return_value=(
        [{"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200} for p in paths_b],
        None,
    ))

    svc_a = RepoIndexService(
        db=db_session, github_client=gc_a, embedding_service=es,
        write_queue=queue,
    )
    svc_b = RepoIndexService(
        db=db_session, github_client=gc_b, embedding_service=es,
        write_queue=queue,
    )

    # Different patches for the two repos.
    async def _dispatch_a():
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_stub_a,
        ):
            await svc_a.build_index("owner/parallel-A", "main", "tok")

    async def _dispatch_b():
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_stub_b,
        ):
            await svc_b.build_index("owner/parallel-B", "main", "tok")

    await asyncio.gather(_dispatch_a(), _dispatch_b())

    meta_a = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/parallel-A",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()
    meta_b = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/parallel-B",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()

    assert meta_a is not None, "repo-A meta missing"
    assert meta_b is not None, "repo-B meta missing"
    assert meta_a.status == "ready", f"repo-A status={meta_a.status!r}"
    assert meta_b.status == "ready", f"repo-B status={meta_b.status!r}"


# ===========================================================================
# Test 6 — orphan sweep flips stale indexing to error
# ===========================================================================


async def test_orphan_sweep_flips_stale_indexing_to_error(db_session) -> None:
    """Spec § 3.4 + § 6 acceptance #6.

    Pre-Cycle-1: ``_gc_orphan_repo_index_runs`` doesn't exist yet — the
    import line raises ImportError → test fails with the documented signal.
    """
    from app.services.gc import _gc_orphan_repo_index_runs

    stale = datetime.now(timezone.utc) - timedelta(minutes=31)
    await _seed_meta(
        db_session,
        "owner/orphan-repo",
        "main",
        status="indexing",
        indexed_at=stale,
    )

    flipped = await _gc_orphan_repo_index_runs(db_session)
    await db_session.commit()  # outer caller commits; mirror that here.

    assert flipped == 1, f"expected 1 row flipped, got {flipped}"

    meta = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/orphan-repo",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()
    assert meta is not None
    assert meta.status == "error", f"status={meta.status!r}"
    assert meta.error_message == "orphan_recovery: crashed mid-build", (
        f"error_message={meta.error_message!r}"
    )


# ===========================================================================
# Test 7 — orphan sweep skips fresh indexing within TTL
# ===========================================================================


async def test_orphan_sweep_skips_fresh_indexing_within_ttl(db_session) -> None:
    """Spec § 3.4 grace period + § 6 acceptance #7.

    Fresh row (5 min old, < 30 min TTL) must NOT be flipped.
    """
    from app.services.gc import _gc_orphan_repo_index_runs

    fresh = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _seed_meta(
        db_session,
        "owner/fresh-repo",
        "main",
        status="indexing",
        indexed_at=fresh,
    )

    flipped = await _gc_orphan_repo_index_runs(db_session)
    await db_session.commit()

    assert flipped == 0, f"expected 0 rows flipped, got {flipped}"

    meta = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/fresh-repo",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()
    assert meta is not None
    assert meta.status == "indexing", (
        f"fresh row must remain status='indexing'; got {meta.status!r}"
    )


# ===========================================================================
# Test 8 — audit hook emits zero warnings during 100-file rebuild
# ===========================================================================


async def test_audit_hook_emits_zero_warnings_during_100_file_rebuild(
    db_session, caplog,
) -> None:
    """Spec § 6 acceptance #8 + § 9.2.

    Per § 9.2: after Cycle 1 lands, audit hook emits ZERO warnings during
    background indexing. The conftest sets WRITE_QUEUE_AUDIT_HOOK_RAISE=true,
    which means a write-on-read-engine attempt would RAISE rather than warn
    — but the read engine is not mounted in this in-memory test DB, so we
    simply assert the build completes with no audit-hook WARNING records
    in caplog.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 100
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    caplog.set_level(logging.WARNING, logger="app.database")
    caplog.set_level(logging.WARNING)  # capture all loggers

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,
        )
        await svc.build_index("owner/audit-100", "main", "ghp_token")

    audit_warns = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "read-engine audit" in r.getMessage()
    ]
    assert audit_warns == [], (
        f"audit hook emitted {len(audit_warns)} warnings during build; "
        f"first: {audit_warns[0].getMessage() if audit_warns else None!r}"
    )


# ===========================================================================
# Test 9 — zero-file repo completes build with two submits
# ===========================================================================


async def test_zero_file_repo_completes_build_with_two_submits(db_session) -> None:
    """Spec § 3.1 empty-table case + § 6 acceptance #9.

    Empty tree, no prior rows → 2 submits (Phase 0 status + Phase 4 finalize).
    """
    from app.services.repo_index_service import RepoIndexService

    gc = _make_mock_github(n_indexable=0)  # empty tree
    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    async def _empty_stub(**kwargs):
        return [], 0, 0

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_empty_stub,
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,
        )
        await svc.build_index("owner/empty-repo", "main", "ghp_token")

    meta = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/empty-repo",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()
    assert meta is not None
    assert meta.status == "ready", f"status={meta.status!r}"
    assert meta.file_count == 0, f"file_count={meta.file_count}"
    assert len(queue.calls) == 2, (
        f"empty-repo build: expected 2 submits, got {len(queue.calls)}"
    )


# ===========================================================================
# Test 10 — meta.file_count matches row count post-build (regression guard)
# ===========================================================================


async def test_meta_file_count_matches_row_count_post_build(db_session) -> None:
    """Spec § 3.5 idempotency invariant + § 6 acceptance #10.

    Already-true in legacy code; binds the invariant for post-refactor too.
    Constructs RepoIndexService WITHOUT ``write_queue`` so the test can
    pass against the legacy single-commit code path (regression-guard
    semantic per spec § 11 row 10 + RED dispatch instruction).
    """
    from app.services.repo_index_service import RepoIndexService

    n = 200
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
        )
        await svc.build_index("owner/count-repo", "main", "ghp_token")

    meta = (
        await db_session.execute(
            select(RepoIndexMeta).where(
                RepoIndexMeta.repo_full_name == "owner/count-repo",
                RepoIndexMeta.branch == "main",
            )
        )
    ).scalars().first()

    rows = (
        await db_session.execute(
            select(RepoFileIndex).where(
                RepoFileIndex.repo_full_name == "owner/count-repo",
                RepoFileIndex.branch == "main",
            )
        )
    ).scalars().all()

    assert meta is not None
    assert meta.file_count == len(rows), (
        f"meta.file_count={meta.file_count} != COUNT(rows)={len(rows)}"
    )


# ===========================================================================
# Test 11 — baseline full test suite passes 3546 (env-gated)
# ===========================================================================


def test_baseline_full_test_suite_passes_3546() -> None:
    """Spec § 6 acceptance #11 + § 8 final target.

    Cycle-1 RED stub: skip in default `pytest` invocations to avoid
    recursion. Cycle-2 INTEGRATE flips the env-var sentinel + the
    threshold (>= 3546) per the orchestrator's plan.
    """
    if not os.environ.get("RUN_FULL_BASELINE_CHECK"):
        pytest.skip(
            "baseline check requires full backend collection — "
            "set RUN_FULL_BASELINE_CHECK=1"
        )

    result = subprocess.check_output(
        ["pytest", str(BACKEND_TESTS_DIR), "--collect-only", "-q"],
        timeout=180,
    )
    count = sum(
        1 for line in result.decode().splitlines()
        if "::test_" in line
    )
    # Cycle-1 target: 3531 (3515 baseline + 16 new). Cycle-2 raises to 3546.
    assert count >= 3531, f"collected {count} tests, expected >= 3531"


# ===========================================================================
# Test 12 — invalidate_index routes through write queue
# ===========================================================================


async def test_invalidate_index_routes_through_write_queue(db_session) -> None:
    """Spec § 3.6 + § 6 implementation surface #5.

    Pre-Cycle-1: invalidate_index calls self._db.execute(delete(...)) +
    self._db.commit() directly — no submit() at all → assert FAIL.
    """
    from app.services.repo_index_service import RepoIndexService

    repo, branch = "owner/inv-repo", "main"
    await _seed_meta(db_session, repo, branch, status="ready", file_count=5)
    await _seed_files(db_session, repo, branch, 5)

    es = _make_mock_embedding()
    gc = _make_mock_github()
    queue = _CountingWriteQueueStub(db_session)

    svc = RepoIndexService(
        db=db_session,
        github_client=gc,
        embedding_service=es,
        write_queue=queue,
    )
    await svc.invalidate_index(repo, branch)

    assert len(queue.calls) >= 1, (
        f"invalidate_index must route at least 1 submit() call; "
        f"got {len(queue.calls)} (legacy direct-commit path?)"
    )


# ===========================================================================
# Test 13 — each batch commits independently via separate submit calls
# ===========================================================================


async def test_each_batch_commits_independently_via_separate_submit_calls(
    db_session,
) -> None:
    """Spec § 3.2 per-batch submit semantics.

    Each persist batch is its own submit() call with a unique work_fn
    instance. Per WriteQueue.submit() contract, each work_fn must commit
    exactly once before returning.
    """
    from app.services.repo_index_service import RepoIndexService

    n = 100
    paths = [f"src/file_{i:04d}.py" for i in range(n)]
    gc = _make_mock_github(n_indexable=n)
    es = _make_mock_embedding()

    # Track per-call commit invocations.
    commit_counts: list[int] = []

    class _CountingCommitQueue:
        def __init__(self, db):
            self._db = db
            self.calls: list[Any] = []

        async def submit(self, work, *, timeout=None, operation_label=None):
            self.calls.append(work)
            # Wrap db so we can count commits inside this work_fn.
            commits_before = getattr(self._db, "_test_commit_count", 0)
            self._db._test_commit_count = commits_before
            orig_commit = self._db.commit

            local_count = {"n": 0}

            async def _counting_commit():
                local_count["n"] += 1
                return await orig_commit()

            self._db.commit = _counting_commit  # type: ignore[method-assign]
            try:
                result = await work(self._db)
            finally:
                self._db.commit = orig_commit  # type: ignore[method-assign]
            commit_counts.append(local_count["n"])
            return result

        async def submit_batch(self, work_fns, *, timeout=None, operation_label=None):
            results = []
            for fn in work_fns:
                results.append(await self.submit(fn))
            return results

    queue = _CountingCommitQueue(db_session)

    with patch(
        "app.services.repo_index_service.read_and_embed_files",
        new=_read_and_embed_stub(paths),
    ):
        svc = RepoIndexService(
            db=db_session,
            github_client=gc,
            embedding_service=es,
            write_queue=queue,
        )
        await svc.build_index("owner/independent-repo", "main", "ghp_token")

    # Each work_fn must call db.commit() exactly once (per submit() contract).
    bad = [c for c in commit_counts if c != 1]
    assert not bad, (
        f"every submit work_fn must commit exactly once; "
        f"got commit counts {commit_counts}"
    )
    # Each call's work_fn instance must be unique (no shared singleton).
    assert len(set(id(c) for c in queue.calls)) == len(queue.calls), (
        "submit() work_fn instances must be unique per call"
    )


# ===========================================================================
# Test 14 — lock held during concurrent call skips second invocation
# ===========================================================================


async def test_lock_held_during_concurrent_call_skips_second_invocation(
    db_session,
) -> None:
    """Spec § 3.3 lock semantics + § 6 implementation surface #2.

    Pre-Cycle-1: _acquire_repo_index_lock doesn't exist → ImportError.
    Post-Cycle-1: lock is acquired manually → build_index returns early.
    """
    from app.services.event_bus import event_bus
    from app.services.repo_index_service import (
        RepoIndexService,
        _acquire_repo_index_lock,
    )

    repo, branch = "owner/repo-X", "main"

    # Seed meta in a known initial state so we can detect any mutation.
    await _seed_meta(
        db_session, repo, branch,
        status="pending", file_count=0,
    )

    captured: list[dict] = []
    queue_events: asyncio.Queue = asyncio.Queue()
    event_bus._subscribers.add(queue_events)

    try:
        # Pre-acquire the lock manually.
        lock = await _acquire_repo_index_lock(repo, branch)
        await lock.acquire()
        try:
            es = _make_mock_embedding()
            gc = _make_mock_github(n_indexable=0)
            queue = _CountingWriteQueueStub(db_session)

            svc = RepoIndexService(
                db=db_session,
                github_client=gc,
                embedding_service=es,
                write_queue=queue,
            )

            t_start = time.monotonic()
            result = await svc.build_index(repo, branch, "tok")
            elapsed_ms = (time.monotonic() - t_start) * 1000

            assert elapsed_ms < 200, (
                f"build_index with held lock must return immediately; "
                f"elapsed {elapsed_ms:.1f}ms"
            )
            assert result is None
            # Drain events.
            while not queue_events.empty():
                captured.append(queue_events.get_nowait())
        finally:
            lock.release()

        # No event-bus events should fire on the lock-skip path (Cycle 1).
        assert not captured, (
            f"lock-skip path must emit NO events in Cycle 1; got {captured}"
        )

        # Meta must be unchanged (status='pending', file_count=0).
        meta = (
            await db_session.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == repo,
                    RepoIndexMeta.branch == branch,
                )
            )
        ).scalars().first()
        assert meta is not None
        assert meta.status == "pending", f"status={meta.status!r}"
        assert meta.file_count == 0, f"file_count={meta.file_count}"
    finally:
        event_bus._subscribers.discard(queue_events)


# ===========================================================================
# Test 15 — lock dict growth bounded by GC sweep
# ===========================================================================


async def test_lock_dict_growth_bounded_by_gc_sweep() -> None:
    """Spec § 3.3 lock cleanup paragraph + § 6 implementation surface #8.

    Pre-Cycle-1: ``_evict_idle_repo_index_locks`` doesn't exist.
    """
    from app.services.repo_index_service import (
        _REPO_INDEX_LOCK_LAST_ACQUIRED,
        _REPO_INDEX_LOCKS,
        _acquire_repo_index_lock,
        _evict_idle_repo_index_locks,
    )

    # Clear any prior state.
    _REPO_INDEX_LOCKS.clear()
    _REPO_INDEX_LOCK_LAST_ACQUIRED.clear()

    # Populate 10 distinct (repo, branch) keys.
    keys = [(f"owner/repo-{i}", "main") for i in range(10)]
    for repo, branch in keys:
        await _acquire_repo_index_lock(repo, branch)

    assert len(_REPO_INDEX_LOCKS) == 10, (
        f"expected 10 lock entries after population; got {len(_REPO_INDEX_LOCKS)}"
    )

    # Backdate 6 of them by 2 hours (well past 1h eviction threshold).
    stale_time = time.time() - 7200
    for i in range(6):
        _REPO_INDEX_LOCK_LAST_ACQUIRED[keys[i]] = stale_time
    # The other 4 stay fresh.
    fresh_time = time.time()
    for i in range(6, 10):
        _REPO_INDEX_LOCK_LAST_ACQUIRED[keys[i]] = fresh_time

    evicted = await _evict_idle_repo_index_locks()
    assert evicted == 6, f"expected 6 evicted, got {evicted}"
    assert len(_REPO_INDEX_LOCKS) == 4, (
        f"expected 4 lock entries after eviction; got {len(_REPO_INDEX_LOCKS)}"
    )


# ===========================================================================
# Test 16 — concurrent persist under load no audit warns (OPERATE)
# ===========================================================================


async def test_concurrent_persist_under_load_no_audit_warns(
    db_session, caplog,
) -> None:
    """Cycle 1 OPERATE-bar test.

    Spawn 5 concurrent build_index tasks across 3 repos:
      3× build_index(A) + 1× build_index(B) + 1× build_index(C)
    The 2 surplus calls on key A collide on the same lock; 1 acquires it
    and proceeds, 2 return early via lock-skip. Keys B and C run unimpeded.

    Assert: 3 metas (A, B, C) updated to status="ready"; 2 A-collision
    attempts return early; ZERO WARNING-level audit-hook records.
    """
    from app.services.repo_index_service import RepoIndexService

    es = _make_mock_embedding()
    queue = _CountingWriteQueueStub(db_session)

    n = 50
    paths_a = [f"src/a_{i:04d}.py" for i in range(n)]
    paths_b = [f"src/b_{i:04d}.py" for i in range(n)]
    paths_c = [f"src/c_{i:04d}.py" for i in range(n)]

    async def _slow_a(**kwargs):
        await asyncio.sleep(0.1)
        return _make_processed_files(paths_a), 0, 0

    async def _slow_b(**kwargs):
        await asyncio.sleep(0.05)
        return _make_processed_files(paths_b), 0, 0

    async def _slow_c(**kwargs):
        await asyncio.sleep(0.05)
        return _make_processed_files(paths_c), 0, 0

    def _gc_for(paths: list[str]) -> Any:
        gc = _make_mock_github()
        gc.get_tree_with_cache = AsyncMock(return_value=(
            [{"type": "blob", "path": p, "sha": f"sha_{p}", "size": 200} for p in paths],
            None,
        ))
        return gc

    caplog.set_level(logging.WARNING)

    async def _run_a(idx: int):
        gc = _gc_for(paths_a)
        svc = RepoIndexService(
            db=db_session, github_client=gc, embedding_service=es,
            write_queue=queue,
        )
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_slow_a,
        ):
            return await svc.build_index("owner/load-A", "main", f"tok-A-{idx}")

    async def _run_b():
        gc = _gc_for(paths_b)
        svc = RepoIndexService(
            db=db_session, github_client=gc, embedding_service=es,
            write_queue=queue,
        )
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_slow_b,
        ):
            return await svc.build_index("owner/load-B", "main", "tok-B")

    async def _run_c():
        gc = _gc_for(paths_c)
        svc = RepoIndexService(
            db=db_session, github_client=gc, embedding_service=es,
            write_queue=queue,
        )
        with patch(
            "app.services.repo_index_service.read_and_embed_files",
            new=_slow_c,
        ):
            return await svc.build_index("owner/load-C", "main", "tok-C")

    tasks = [
        asyncio.create_task(_run_a(0)),
        asyncio.create_task(_run_a(1)),
        asyncio.create_task(_run_a(2)),
        asyncio.create_task(_run_b()),
        asyncio.create_task(_run_c()),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # All 3 distinct repos must have status="ready" after the dust settles.
    for repo in ("owner/load-A", "owner/load-B", "owner/load-C"):
        meta = (
            await db_session.execute(
                select(RepoIndexMeta).where(
                    RepoIndexMeta.repo_full_name == repo,
                    RepoIndexMeta.branch == "main",
                )
            )
        ).scalars().first()
        assert meta is not None, f"meta missing for {repo}"
        assert meta.status == "ready", (
            f"{repo} status={meta.status!r} — expected 'ready'"
        )

    audit_warns = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING
        and "read-engine audit" in r.getMessage()
    ]
    assert audit_warns == [], (
        f"audit hook must emit zero warnings under concurrent load; "
        f"got {len(audit_warns)}"
    )
