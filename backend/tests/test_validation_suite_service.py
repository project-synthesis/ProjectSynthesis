"""Cycle 2 + 3 + 4 RED tests for ``ValidationSuiteService`` — 11 + 9 + 7 = 27 tests.

Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 2-4
Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4 + §5 + §9

The Cycle 2 tests (1-11) cover ``ValidationSuiteService.create_from_run``;
Cycle 2 GREEN has landed so they pass.

The Cycle 3 tests (12-20) cover ``retire``, ``get``, ``list``,
``list_replays`` plus the ``SuiteRetireInputs`` frozen dataclass and the
``validation_suite_retire`` write-queue operation label. Cycle 3 GREEN has
landed so they pass.

The Cycle 4 tests (21-27) cover ``compute_regression_alarm`` + the 30s TTL
cache + the ``regression_alarm_transition`` event. Cycle 4 GREEN has NOT
yet landed: these 7 tests fail with ``AttributeError`` (no
``.compute_regression_alarm`` method on the service) or
``AttributeError: '_invalidate_alarm_cache'`` (the test-seam helper that
forces TTL invalidation between calls — see Cycle 4 contract block below).

Per spec §5 the service is detached-ORM-safe (Foundation P4 contract): reads
happen inside a short DB session; the snapshot dataclass crosses the
``WriteQueue.submit()`` boundary; no DB session is held during persistence.
Per spec §9 trace tagging suite creates AND retires emit JSONL with
``phase="validation_suite"``. The ``validation_suite_retired`` event fires
ONLY on first retire — idempotent re-retire of an already-retired suite is
a no-op success and emits ZERO events / ZERO JSONL trace entries.

Cycle 1 already landed:
  * `ValidationSuite` ORM declaration (`app/models.py:675`)
  * Alembic migration `5576c539720f` creating the table + FK + indexes
  * `RunRow.suite_id` FK + `ix_run_row_suite_id` index

Cycle 2 landed:
  * `app/schemas/validation_suite.py` (Pydantic schemas per spec §4)
  * `app/services/validation_suite_service.py` (`ValidationSuiteService` +
    `SuiteSnapshotInputs` per spec §5)
  * Write queue operation label ``"validation_suite_create"``
  * Event ``validation_suite_created`` emitted post-submit success
  * JSONL trace entry with ``phase="validation_suite"``

Cycle 3 must add:
  * ``ValidationSuiteService.retire(suite_id, *, reason, db, write_queue)``
  * ``ValidationSuiteService.get(suite_id, *, db)``
  * ``ValidationSuiteService.list(*, db, project_id=None,
    repo_full_name=None, include_retired=False, limit=20, offset=0)``
  * ``ValidationSuiteService.list_replays(suite_id, *, db, limit=20,
    offset=0)``
  * ``SuiteRetireInputs`` frozen dataclass (spec §5)
  * Write-queue operation label ``"validation_suite_retire"`` — only on
    first retire; idempotent re-retire short-circuits before submit
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, RunRow, ValidationSuite

# The two not-yet-existing modules — every import below raises
# ``ModuleNotFoundError`` until Cycle 2 GREEN lands. Captured here at module
# top so all 11 tests fail at COLLECTION time with the same import error.
from app.schemas.validation_suite import (  # noqa: F401 — Cycle 2/3 RED signal
    BaselineScoresPayload,
    PerPromptScore,
    PromptSnapshotItem,
    RetireSuiteRequest,
    ValidationSuiteListItem,
    ValidationSuiteListResponse,
    ValidationSuiteOut,
)
from app.services.validation_suite_service import (  # noqa: F401 — Cycle 2/3 RED signal
    SuiteSnapshotInputs,
    ValidationSuiteService,
)

# ``SuiteRetireInputs`` is deliberately NOT imported at module top. Adding it
# here before Cycle 3 GREEN lands would break collection of the 11 Cycle 2
# tests with ``ImportError``. The Cycle 3 retire-path tests instead fail at
# AttributeError on the missing ``.retire`` / ``.get`` / ``.list`` /
# ``.list_replays`` method — which is the canonical RED signal the GREEN
# step has not yet implemented those methods + the ``SuiteRetireInputs``
# dataclass + ``_persist_suite_retire`` callback per spec §5.

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local fixtures — mirror ``tests/test_run_orchestrator.py`` shared-URI pattern
# so the WriteQueue's writer engine and the read session see the same data.
# ---------------------------------------------------------------------------


_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_validation_suite_service_test?mode=memory&cache=shared&uri=true"
)


@pytest_asyncio.fixture
async def writer_engine() -> AsyncGenerator[Any, None]:
    """In-memory writer engine bound to a unique shared URI."""
    engine = create_async_engine(_SHARED_URI)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def write_queue(writer_engine):
    """Started WriteQueue bound to the in-memory writer engine."""
    from app.services.write_queue import WriteQueue
    queue = WriteQueue(writer_engine)
    await queue.start()
    try:
        yield queue
    finally:
        await queue.stop(drain_timeout=2.0)


@pytest_asyncio.fixture
async def patched_session_factory(writer_engine, monkeypatch):
    """Repoint ``app.database.async_session_factory`` at the shared in-memory DB.

    ``ValidationSuiteService.create_from_run`` reads ``RunRow`` via the
    canonical ``async_session_factory`` (per spec §5 detached-ORM-safe pattern
    — short read session, no session held during write). Without this patch
    the read goes against production engine and never sees rows the test
    seeded into the writer engine.
    """
    import app.database as database_mod
    new_factory = async_sessionmaker(
        writer_engine, class_=AsyncSession, expire_on_commit=False,
    )
    monkeypatch.setattr(database_mod, "async_session_factory", new_factory)
    yield new_factory


@pytest_asyncio.fixture
async def db(
    writer_engine, patched_session_factory,
) -> AsyncGenerator[AsyncSession, None]:
    """Read session against the same shared URI so writes committed via the
    WriteQueue are immediately visible. Created AFTER ``writer_engine`` so
    schema materialisation has happened."""
    read_engine = create_async_engine(_SHARED_URI)
    factory = async_sessionmaker(
        read_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await read_engine.dispose()


# ---------------------------------------------------------------------------
# Canonical aggregate / prompt_results shape — single source of truth
# ---------------------------------------------------------------------------


def _canonical_aggregate(*, mean=7.85, p5=6.2, p50=7.8, p95=9.1) -> dict:
    """Build a canonical ``RunRow.aggregate`` shape matching the topic_probe
    generator's ``_build_aggregate`` output (see
    ``services/generators/topic_probe_generator.py:_build_aggregate``).

    Canonical keys: ``mean_overall``, ``p5_overall``, ``p50_overall``,
    ``p95_overall``, ``completed_count``, ``failed_count``,
    ``f5_flag_fires``, ``scoring_formula_version``, ``task_type_distribution``.

    The ``per_prompt`` key carries the per-row scoring snapshot — populated
    here as a 3-element list with realistic ``raw_prompt_idx`` /
    ``overall`` / ``dimensions`` triples.
    """
    return {
        "mean_overall": mean,
        "p5_overall": p5,
        "p50_overall": p50,
        "p95_overall": p95,
        "completed_count": 3,
        "failed_count": 0,
        "f5_flag_fires": 0,
        "scoring_formula_version": 4,
        "task_type_distribution": {"coding": 2, "analysis": 1},
        "per_prompt": [
            {
                "raw_prompt_idx": 0, "overall": 8.1,
                "dimensions": {"clarity": 8.0, "specificity": 8.5,
                               "structure": 7.8, "faithfulness": 8.2,
                               "conciseness": 7.9},
            },
            {
                "raw_prompt_idx": 1, "overall": 7.4,
                "dimensions": {"clarity": 7.2, "specificity": 7.8,
                               "structure": 7.0, "faithfulness": 7.5,
                               "conciseness": 7.4},
            },
            {
                "raw_prompt_idx": 2, "overall": 8.0,
                "dimensions": {"clarity": 8.1, "specificity": 8.0,
                               "structure": 7.9, "faithfulness": 8.0,
                               "conciseness": 8.0},
            },
        ],
    }


def _prompt_results(n: int) -> list[dict]:
    """Build N per-prompt result rows.

    Each row carries ``raw_prompt``, ``optimized_prompt`` plus scoring fields
    — the position-correspondence invariant pins
    ``prompts_snapshot[i].raw_prompt == prompt_results[i]['raw_prompt']``.
    """
    return [
        {
            "prompt_idx": i,
            "raw_prompt": f"raw prompt {i}",
            "optimized_prompt": f"optimized prompt {i}",
            "intent_label": "general",
            "overall_score": 7.0 + (i % 3) * 0.5,
            "optimization_id": None,
            "status": "completed",
        }
        for i in range(n)
    ]


async def _seed_run(
    db: AsyncSession,
    *,
    mode: str = "topic_probe",
    status: str = "completed",
    aggregate: dict | None = None,
    prompt_results: list[dict] | None = None,
    project_id: str | None = None,
    repo_full_name: str | None = None,
    run_id: str | None = None,
) -> RunRow:
    """Insert a ``RunRow`` via the read session — both engines share the same
    in-memory DB so the row is visible to ``create_from_run``'s read session
    too."""
    row = RunRow(
        id=run_id or uuid.uuid4().hex,
        mode=mode,
        status=status,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC) if status != "running" else None,
        prompts_generated=len(prompt_results or []),
        prompt_results=prompt_results,
        aggregate=aggregate,
        project_id=project_id,
        repo_full_name=repo_full_name,
    )
    db.add(row)
    await db.commit()
    # Re-read so the caller gets a fully-hydrated row.
    refreshed = (
        await db.execute(select(RunRow).where(RunRow.id == row.id))
    ).scalar_one()
    return refreshed


# ===========================================================================
# Test 1 — happy path: create_from_run on completed topic_probe run
# ===========================================================================


async def test_create_from_run_happy_path(db: AsyncSession, write_queue):
    """Spec §5 happy path — completed topic_probe + valid aggregate produces a
    persisted suite with a returned ValidationSuiteOut whose fields mirror the
    snapshot inputs."""
    aggregate = _canonical_aggregate(mean=7.85, p5=6.2, p95=9.1)
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
        repo_full_name="acme/widget",
    )

    service = ValidationSuiteService()
    out = await service.create_from_run(
        run_id=run.id, label="test", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    # Return value contract
    assert isinstance(out, ValidationSuiteOut), (
        f"create_from_run must return a Pydantic ValidationSuiteOut, "
        f"got {type(out)!r}"
    )
    assert out.label == "test"
    assert out.tolerance_abs == 0.5
    assert out.source_run_id == run.id
    assert isinstance(out.prompts_snapshot, list)
    assert len(out.prompts_snapshot) == len(prompt_results)
    assert all(
        isinstance(item, PromptSnapshotItem) for item in out.prompts_snapshot
    ), "prompts_snapshot entries must be PromptSnapshotItem instances"
    assert isinstance(out.baseline_scores, BaselineScoresPayload)
    assert out.baseline_scores.mean_overall == 7.85

    # Persistence contract — row exists in DB with matching id
    await db.commit()  # flush read-session state so writer commits are visible
    persisted = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == out.id),
        )
    ).scalar_one()
    assert persisted.label == "test"
    assert persisted.tolerance_abs == 0.5
    assert persisted.source_run_id == run.id


# ===========================================================================
# Test 2 — run_not_found
# ===========================================================================


async def test_create_from_run_raises_run_not_found_on_missing_id(
    db: AsyncSession, write_queue,
):
    """Spec §4 error envelope — missing run_id raises ``run_not_found``."""
    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="run_not_found"):
        await service.create_from_run(
            run_id=uuid.uuid4().hex,  # unseeded
            label="test", tolerance_abs=0.5,
            db=db, write_queue=write_queue,
        )


# ===========================================================================
# Test 3 — run_not_completed (status='running')
# ===========================================================================


async def test_create_from_run_raises_run_not_completed_on_running_status(
    db: AsyncSession, write_queue,
):
    """Spec §4 error envelope — non-completed run raises ``run_not_completed``."""
    run = await _seed_run(
        db, status="running", aggregate=None, prompt_results=None,
    )

    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="run_not_completed"):
        await service.create_from_run(
            run_id=run.id, label="test", tolerance_abs=0.5,
            db=db, write_queue=write_queue,
        )


# ===========================================================================
# Test 4 — not_a_probe_run (mode='seed_agent')
# ===========================================================================


async def test_create_from_run_raises_not_a_probe_run_on_seed_agent_mode(
    db: AsyncSession, write_queue,
):
    """Spec §4 error envelope + §3 key invariant 5 — only topic_probe runs
    forkable. Seed agent runs raise ``not_a_probe_run``."""
    aggregate = _canonical_aggregate()
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, mode="seed_agent", aggregate=aggregate,
        prompt_results=prompt_results,
    )

    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="not_a_probe_run"):
        await service.create_from_run(
            run_id=run.id, label="test", tolerance_abs=0.5,
            db=db, write_queue=write_queue,
        )


# ===========================================================================
# Test 5 — not_a_probe_run (mode='replay_run') — recursive fork guard
# ===========================================================================


async def test_create_from_run_raises_not_a_probe_run_on_replay_run_mode(
    db: AsyncSession, write_queue,
):
    """Spec §3 key invariant 5 — replay_run outputs MUST NOT be forkable into
    new suites (prevents recursive forking). ``mode`` column is a free
    ``String`` at the DB level so 'replay_run' is storable even before the
    Pydantic Literal extension lands."""
    aggregate = _canonical_aggregate()
    prompt_results = _prompt_results(3)
    # Insert directly via the session — mode='replay_run' is DB-storable.
    run = await _seed_run(
        db, mode="replay_run", aggregate=aggregate,
        prompt_results=prompt_results,
    )

    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="not_a_probe_run"):
        await service.create_from_run(
            run_id=run.id, label="test", tolerance_abs=0.5,
            db=db, write_queue=write_queue,
        )


# ===========================================================================
# Test 6 — run_missing_aggregate
# ===========================================================================


async def test_create_from_run_raises_run_missing_aggregate(
    db: AsyncSession, write_queue,
):
    """Spec §4 error envelope — completed topic_probe with ``aggregate=None``
    or missing ``mean_overall`` raises ``run_missing_aggregate``. Covers
    legacy / corrupted run rows where the aggregate column never materialised."""
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, status="completed", aggregate=None,
        prompt_results=prompt_results,
    )

    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="run_missing_aggregate"):
        await service.create_from_run(
            run_id=run.id, label="test", tolerance_abs=0.5,
            db=db, write_queue=write_queue,
        )


# ===========================================================================
# Test 7 — position correspondence (per_prompt[i] ↔ prompts_snapshot[i])
# ===========================================================================


async def test_create_from_run_position_correspondence_preserved(
    db: AsyncSession, write_queue,
):
    """Spec §3 key invariant 2 — position correspondence
    ``baseline_scores.per_prompt[i] ↔ prompts_snapshot[i] ↔
    RunRow.prompt_results[i]``. Frozen at save time."""
    # Need 10 distinct prompts — generate a 10-element per_prompt list.
    aggregate = _canonical_aggregate()
    aggregate["per_prompt"] = [
        {
            "raw_prompt_idx": i, "overall": 7.0 + i * 0.1,
            "dimensions": {
                "clarity": 7.0 + i * 0.1, "specificity": 7.0 + i * 0.1,
                "structure": 7.0 + i * 0.1, "faithfulness": 7.0 + i * 0.1,
                "conciseness": 7.0 + i * 0.1,
            },
        }
        for i in range(10)
    ]
    aggregate["completed_count"] = 10
    prompt_results = _prompt_results(10)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
    )

    service = ValidationSuiteService()
    out = await service.create_from_run(
        run_id=run.id, label="position", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    assert len(out.prompts_snapshot) == 10
    for i in range(10):
        assert out.prompts_snapshot[i].raw_prompt == prompt_results[i]["raw_prompt"], (
            f"position correspondence broken at i={i}: "
            f"snapshot={out.prompts_snapshot[i].raw_prompt!r} != "
            f"prompt_results[{i}].raw_prompt={prompt_results[i]['raw_prompt']!r}"
        )


# ===========================================================================
# Test 8 — operation_label='validation_suite_create' surfaces on submit
# ===========================================================================


async def test_create_from_run_uses_validation_suite_create_operation_label(
    db: AsyncSession, write_queue, monkeypatch,
):
    """Spec §4 write-queue operation labels — ``create_from_run`` calls
    ``WriteQueue.submit`` with ``operation_label='validation_suite_create'``
    for JSONL trace filterability."""
    aggregate = _canonical_aggregate()
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
    )

    captured: list[dict] = []

    real_submit = write_queue.submit

    async def _capturing_submit(work, *, timeout=None, operation_label=None):
        captured.append(
            {"operation_label": operation_label, "timeout": timeout},
        )
        return await real_submit(work, timeout=timeout, operation_label=operation_label)

    monkeypatch.setattr(write_queue, "submit", _capturing_submit)

    service = ValidationSuiteService()
    await service.create_from_run(
        run_id=run.id, label="op-label", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    labels = [c["operation_label"] for c in captured]
    assert "validation_suite_create" in labels, (
        f"expected operation_label='validation_suite_create' to appear in "
        f"submit calls; got labels={labels!r}"
    )


# ===========================================================================
# Test 9 — detached-ORM safety: session.close BEFORE WriteQueue.submit
# ===========================================================================


async def test_create_from_run_detached_orm_safe(
    db: AsyncSession, write_queue, monkeypatch,
):
    """Foundation P4 anti-pattern A2 contract — NO DB session held while
    ``WriteQueue.submit()`` runs.

    Mechanism: ``create_from_run`` opens a short read session via
    ``async_session_factory``, builds the frozen ``SuiteSnapshotInputs``
    dataclass, closes the read session, THEN calls ``write_queue.submit``.

    The patched session factory's ``close()`` method records call order; the
    patched ``write_queue.submit`` records call order. ``close()`` MUST fire
    BEFORE ``submit()``.
    """
    aggregate = _canonical_aggregate()
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
    )

    call_order: list[str] = []

    # Wrap the session factory's __aexit__ (which calls .close on the
    # underlying session) to record the close event in order.
    import app.database as database_mod
    real_factory = database_mod.async_session_factory

    class _OrderedFactory:
        def __init__(self, inner):
            self._inner = inner

        def __call__(self, *args, **kwargs):
            inner_ctx = self._inner(*args, **kwargs)
            real_aexit = inner_ctx.__aexit__

            async def _aexit_recorder(exc_type, exc, tb):
                # Record close BEFORE delegating so we capture the moment the
                # service hands off the session — service-side close happens
                # via this context-manager exit.
                call_order.append("session_close")
                return await real_aexit(exc_type, exc, tb)

            inner_ctx.__aexit__ = _aexit_recorder
            return inner_ctx

    monkeypatch.setattr(
        database_mod, "async_session_factory", _OrderedFactory(real_factory),
    )

    real_submit = write_queue.submit

    async def _ordered_submit(work, *, timeout=None, operation_label=None):
        call_order.append("write_queue_submit")
        return await real_submit(
            work, timeout=timeout, operation_label=operation_label,
        )

    monkeypatch.setattr(write_queue, "submit", _ordered_submit)

    service = ValidationSuiteService()
    await service.create_from_run(
        run_id=run.id, label="detached", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    # Find the close + submit positions; close MUST precede submit
    close_idx = next(
        (i for i, e in enumerate(call_order) if e == "session_close"), None,
    )
    submit_idx = next(
        (i for i, e in enumerate(call_order) if e == "write_queue_submit"),
        None,
    )
    assert close_idx is not None, (
        f"session_close never recorded; call_order={call_order!r}. "
        f"create_from_run must use ``async with async_session_factory() as db``."
    )
    assert submit_idx is not None, (
        f"write_queue_submit never recorded; call_order={call_order!r}."
    )
    assert close_idx < submit_idx, (
        f"detached-ORM contract violated — session closed at index "
        f"{close_idx} but WriteQueue.submit fired at index {submit_idx}. "
        f"Foundation P4 requires session.close BEFORE submit. "
        f"call_order={call_order!r}"
    )


# ===========================================================================
# Test 10 — validation_suite_created event + phase="validation_suite" trace
# ===========================================================================


async def test_create_from_run_emits_validation_suite_created_event(
    db: AsyncSession, write_queue, monkeypatch, tmp_path,
):
    """Spec §9 observability — exactly one ``validation_suite_created`` event
    with payload fields ``suite_id, source_run_id, label, tolerance_abs,
    prompts_count, baseline_mean, project_id``. ALSO assert a JSONL trace
    entry was written with ``phase="validation_suite"``."""
    # Isolate trace output directory
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)

    aggregate = _canonical_aggregate(mean=7.85)
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
        project_id=None,  # avoid prompt_cluster FK setup
    )

    # Capture event_bus.publish calls
    from app.services.event_bus import event_bus
    captured: list[tuple[str, dict]] = []
    real_publish = event_bus.publish

    def _capturing_publish(event_type, data):
        captured.append((event_type, dict(data) if isinstance(data, dict) else {}))
        return real_publish(event_type, data)

    monkeypatch.setattr(event_bus, "publish", _capturing_publish)

    service = ValidationSuiteService()
    out = await service.create_from_run(
        run_id=run.id, label="evented", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    # Filter to validation_suite_created — exactly one
    suite_events = [
        payload for (etype, payload) in captured
        if etype == "validation_suite_created"
    ]
    assert len(suite_events) == 1, (
        f"expected exactly 1 validation_suite_created event, got "
        f"{len(suite_events)}; captured events: "
        f"{[e for (e, _) in captured]!r}"
    )

    payload = suite_events[0]
    expected_keys = {
        "suite_id", "source_run_id", "label", "tolerance_abs",
        "prompts_count", "baseline_mean", "project_id",
    }
    missing = expected_keys - payload.keys()
    assert not missing, (
        f"validation_suite_created event missing required keys: "
        f"{sorted(missing)}; payload={payload!r}"
    )
    assert payload["suite_id"] == out.id
    assert payload["source_run_id"] == run.id
    assert payload["label"] == "evented"
    assert payload["tolerance_abs"] == 0.5
    assert payload["prompts_count"] == len(prompt_results)
    assert payload["baseline_mean"] == 7.85

    # JSONL trace — scan traces directory for phase="validation_suite" entry
    traces_dir = tmp_path / "traces"
    assert traces_dir.exists(), (
        f"expected traces directory {traces_dir} to be created during "
        f"create_from_run; got tmp_path contents: "
        f"{sorted(p.name for p in tmp_path.iterdir())}"
    )
    import json
    matching_entries: list[dict] = []
    for jsonl_path in traces_dir.glob("*.jsonl"):
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("phase") == "validation_suite":
                matching_entries.append(entry)

    assert matching_entries, (
        f"expected at least one JSONL trace entry with "
        f"phase='validation_suite' per spec §9 trace tagging; "
        f"found none in {traces_dir}"
    )


# ===========================================================================
# Test 11 — baseline_scores uses canonical key p5_overall (NOT p5)
# ===========================================================================


async def test_create_from_run_baseline_scores_uses_p5_overall_not_p5(
    db: AsyncSession, write_queue,
):
    """Spec §3 baseline_scores shape — key names match canonical
    ``compute_run_aggregate`` output verbatim. ``p5_overall``, NOT ``p5``."""
    aggregate = _canonical_aggregate(mean=8.0, p5=6.2, p50=8.0, p95=9.4)
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
    )

    service = ValidationSuiteService()
    out = await service.create_from_run(
        run_id=run.id, label="canonical", tolerance_abs=0.5,
        db=db, write_queue=write_queue,
    )

    # baseline_scores is a Pydantic BaselineScoresPayload — canonical attr
    # name is p5_overall. Accessing .p5 would raise AttributeError (correct).
    assert out.baseline_scores.p5_overall == 6.2, (
        f"baseline_scores.p5_overall must equal source aggregate's p5_overall "
        f"(canonical key, NEVER 'p5'); got {out.baseline_scores.p5_overall!r}"
    )

    # Hard assertion that the schema does NOT expose a non-canonical 'p5' alias
    assert not hasattr(out.baseline_scores, "p5"), (
        "BaselineScoresPayload must NOT expose a 'p5' attribute — spec §3 "
        "pins the canonical key to 'p5_overall' to preserve downstream "
        "consumer compatibility with RunRow.aggregate readers."
    )


# ###########################################################################
# Cycle 3 — retire / get / list / list_replays — 9 tests
# ###########################################################################
#
# Plan: docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md Cycle 3 Task 3.1
# Spec: docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md §4 + §5
#       + §9 (validation_suite_retired event)
#
# Pre-condition before Cycle 3 GREEN lands:
#   - ``ValidationSuiteService`` has no ``.retire`` / ``.get`` / ``.list`` /
#     ``.list_replays`` methods → AttributeError at attribute access
#   - ``SuiteRetireInputs`` does not exist in service module → ImportError at
#     lazy import inside the test that needs it
#
# Each Cycle 3 test seeds a Cycle 2 suite via ``create_from_run`` so the
# state machine progresses through canonical create → retire / get / list
# paths. Helpers reused: ``_seed_run``, ``_canonical_aggregate``,
# ``_prompt_results``, ``db`` fixture, ``write_queue`` fixture.
# ###########################################################################


async def _seed_suite(
    db: AsyncSession,
    write_queue,
    *,
    label: str = "cycle-3-fixture",
    tolerance_abs: float = 0.5,
    repo_full_name: str | None = None,
) -> ValidationSuiteOut:
    """Helper — seed a canonical RunRow + create a suite from it.

    Returns the ValidationSuiteOut returned by ``create_from_run`` so callers
    can pluck out the persisted ``id``. Shared by every Cycle 3 test that
    needs at least one suite in the DB.
    """
    aggregate = _canonical_aggregate()
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db,
        aggregate=aggregate,
        prompt_results=prompt_results,
        repo_full_name=repo_full_name,
    )
    service = ValidationSuiteService()
    return await service.create_from_run(
        run_id=run.id,
        label=label,
        tolerance_abs=tolerance_abs,
        db=db,
        write_queue=write_queue,
    )


# ===========================================================================
# Test 12 (Cycle 3 #1) — retire writes retired_at + retired_reason
# ===========================================================================


async def test_retire_sets_retired_at_and_reason_writeonly(
    db: AsyncSession, write_queue,
):
    """Spec §5 retire body — first retire MUST persist both ``retired_at``
    (datetime, NOT None) and ``retired_reason`` (the caller-supplied string).

    DB query is the canonical evidence — never trust the service's return
    value as proof of persistence (Foundation P4 OPERATE/O1).
    """
    suite_out = await _seed_suite(db, write_queue)

    service = ValidationSuiteService()
    await service.retire(
        suite_id=suite_out.id,
        reason="superseded by new baseline",
        db=db,
        write_queue=write_queue,
    )

    await db.commit()  # Flush read-session so writer commits are visible.
    row = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_out.id),
        )
    ).scalar_one()
    assert isinstance(row.retired_at, datetime), (
        f"expected retired_at to be a datetime after first retire; got "
        f"{row.retired_at!r}"
    )
    assert row.retired_reason == "superseded by new baseline", (
        f"expected retired_reason to be the caller-supplied string; got "
        f"{row.retired_reason!r}"
    )


# ===========================================================================
# Test 13 (Cycle 3 #2) — retire MUST NOT mutate other columns
# ===========================================================================


async def test_retire_does_not_mutate_other_columns(
    db: AsyncSession, write_queue,
):
    """Spec §10 Cycle 3 INTEGRATE A4 — retire updates ONLY ``retired_at`` and
    ``retired_reason``; column-by-column post-update diff confirms every
    other column is byte-identical to its pre-retire value.

    The columns scanned cover every attribute on the ``ValidationSuite`` ORM
    model (``app/models.py:675``): id, source_run_id, prompts_snapshot,
    baseline_scores, tolerance_abs, label, project_id, repo_full_name,
    created_at.
    """
    suite_out = await _seed_suite(
        db, write_queue, label="diff-fixture", tolerance_abs=0.7,
        repo_full_name="acme/widget",
    )

    # Snapshot the row BEFORE retire — read every persisted column.
    await db.commit()
    before = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_out.id),
        )
    ).scalar_one()
    saved = {
        "id": before.id,
        "source_run_id": before.source_run_id,
        "prompts_snapshot": before.prompts_snapshot,
        "baseline_scores": before.baseline_scores,
        "tolerance_abs": before.tolerance_abs,
        "label": before.label,
        "project_id": before.project_id,
        "repo_full_name": before.repo_full_name,
        "created_at": before.created_at,
    }
    # Expire the in-session view so the post-retire SELECT goes to the DB.
    db.expire(before)

    service = ValidationSuiteService()
    await service.retire(
        suite_id=suite_out.id,
        reason="diff-test reason",
        db=db,
        write_queue=write_queue,
    )

    await db.commit()
    after = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_out.id),
        )
    ).scalar_one()

    # Column-by-column diff — every non-retire column must match the saved
    # snapshot exactly. Catches the A4 anti-pattern (retire accidentally
    # rewriting label, tolerance_abs, or snapshot JSON columns).
    assert after.id == saved["id"], "id mutated"
    assert after.source_run_id == saved["source_run_id"], "source_run_id mutated"
    assert after.prompts_snapshot == saved["prompts_snapshot"], (
        "prompts_snapshot mutated"
    )
    assert after.baseline_scores == saved["baseline_scores"], (
        "baseline_scores mutated"
    )
    assert after.tolerance_abs == saved["tolerance_abs"], "tolerance_abs mutated"
    assert after.label == saved["label"], "label mutated"
    assert after.project_id == saved["project_id"], "project_id mutated"
    assert after.repo_full_name == saved["repo_full_name"], (
        "repo_full_name mutated"
    )
    assert after.created_at == saved["created_at"], "created_at mutated"

    # And the retire columns DID transition.
    assert after.retired_at is not None, (
        "retired_at must be populated after first retire"
    )
    assert after.retired_reason == "diff-test reason", (
        f"retired_reason should be the caller-supplied string; got "
        f"{after.retired_reason!r}"
    )


# ===========================================================================
# Test 14 (Cycle 3 #3) — retire is idempotent on already-retired suites
# ===========================================================================


async def test_retire_is_idempotent_on_already_retired(
    db: AsyncSession, write_queue,
):
    """Spec §5 retire body — re-retire of an already-retired suite is a no-op
    success. The persisted ``retired_at`` MUST remain the first-retire
    timestamp; the persisted ``retired_reason`` MUST remain the first reason.

    Brief async sleep between retire calls guarantees that a buggy
    overwriting implementation would produce a DETECTABLY different
    ``retired_at`` (datetime.utcnow precision is microsecond — 10ms gap is
    plenty to fail a passing-on-luck implementation).
    """
    import asyncio
    suite_out = await _seed_suite(db, write_queue)

    service = ValidationSuiteService()

    # First retire — captures the canonical timestamp + reason.
    await service.retire(
        suite_id=suite_out.id,
        reason="initial retire reason",
        db=db,
        write_queue=write_queue,
    )

    await db.commit()
    row_1 = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_out.id),
        )
    ).scalar_one()
    retired_at_1 = row_1.retired_at
    retired_reason_1 = row_1.retired_reason
    assert retired_at_1 is not None
    assert retired_reason_1 == "initial retire reason"
    db.expire(row_1)

    # Detectable delay — guarantees subsequent retire would produce a
    # different timestamp if the implementation mistakenly overwrites.
    await asyncio.sleep(0.01)

    # Second retire — different reason, must NOT overwrite.
    await service.retire(
        suite_id=suite_out.id,
        reason="second retire reason (must be ignored)",
        db=db,
        write_queue=write_queue,
    )

    await db.commit()
    row_2 = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_out.id),
        )
    ).scalar_one()
    assert row_2.retired_at == retired_at_1, (
        f"retired_at changed between calls — idempotency broken. "
        f"first={retired_at_1!r}, second={row_2.retired_at!r}"
    )
    assert row_2.retired_reason == retired_reason_1, (
        f"retired_reason changed between calls — idempotency broken. "
        f"first={retired_reason_1!r}, second={row_2.retired_reason!r}"
    )


# ===========================================================================
# Test 15 (Cycle 3 #4) — re-retire emits ZERO events + ZERO JSONL traces
# ===========================================================================


async def test_retire_idempotent_no_event_emitted_on_re_retire(
    db: AsyncSession, write_queue, monkeypatch, tmp_path,
):
    """Spec §9 observability — ``validation_suite_retired`` event fires ONLY
    when state actually transitions. The first retire emits exactly ONE
    event AND ONE JSONL trace entry with ``phase="validation_suite"``.
    The second retire (already-retired suite) emits ZERO events AND ZERO
    JSONL entries — the short-circuit return happens BEFORE
    ``WriteQueue.submit()`` and BEFORE any event/trace emission.

    Trace inspection follows the canonical Cycle 2 Test 10 pattern:
    monkeypatch ``app.config.DATA_DIR`` to ``tmp_path``, run the operation,
    glob ``traces_dir`` for JSONL files with ``phase="validation_suite"``.
    """
    # Isolate trace output directory — TraceLogger reads DATA_DIR at call
    # time per validation_suite_service.py:245.
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "DATA_DIR", tmp_path)

    suite_out = await _seed_suite(db, write_queue)

    # Subscribe to event bus before the FIRST retire so we capture the
    # emit-on-transition event AND can reset for the no-op re-retire.
    from app.services.event_bus import event_bus
    captured: list[tuple[str, dict]] = []
    real_publish = event_bus.publish

    def _capturing_publish(event_type, data):
        captured.append(
            (event_type, dict(data) if isinstance(data, dict) else {}),
        )
        return real_publish(event_type, data)

    monkeypatch.setattr(event_bus, "publish", _capturing_publish)

    service = ValidationSuiteService()
    # ---- FIRST retire: state transitions, MUST emit event + JSONL ----
    await service.retire(
        suite_id=suite_out.id,
        reason="first retire — emits event",
        db=db,
        write_queue=write_queue,
    )

    suite_events_first = [
        payload for (etype, payload) in captured
        if etype == "validation_suite_retired"
    ]
    assert len(suite_events_first) == 1, (
        f"first retire MUST emit exactly 1 validation_suite_retired event; "
        f"got {len(suite_events_first)}. all captured types: "
        f"{[e for (e, _) in captured]!r}"
    )

    # JSONL trace inspection — at least 1 retire-phase entry exists after
    # the first retire. Use a fresh helper because the suite_create trace
    # is ALSO emitted to the same directory by the create_from_run path.
    import json

    def _count_validation_suite_traces() -> list[dict]:
        traces_dir = tmp_path / "traces"
        entries: list[dict] = []
        if not traces_dir.exists():
            return entries
        for jsonl_path in traces_dir.glob("*.jsonl"):
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("phase") != "validation_suite":
                    continue
                # ``action`` lives inside the trace's result block per
                # validation_suite_service.py:256 — TraceLogger.log_phase
                # passes the result dict through unchanged. Both ``create``
                # and ``retire`` actions emit phase=validation_suite; we
                # filter to retire entries by inspecting result.action.
                result = e.get("result", {}) or {}
                if result.get("action") == "retire":
                    entries.append(e)
        return entries

    retire_traces_first = _count_validation_suite_traces()
    assert len(retire_traces_first) == 1, (
        f"first retire MUST emit exactly 1 JSONL trace with phase="
        f"'validation_suite' and result.action='retire'; got "
        f"{len(retire_traces_first)}. traces dir contents: "
        f"{list((tmp_path / 'traces').glob('*.jsonl')) if (tmp_path / 'traces').exists() else 'no dir'}"
    )

    # ---- RESET — clear event capture so the re-retire is isolated ----
    captured.clear()

    # ---- SECOND retire (already retired): MUST emit ZERO events ----
    await service.retire(
        suite_id=suite_out.id,
        reason="second retire — must NOT emit event",
        db=db,
        write_queue=write_queue,
    )

    suite_events_second = [
        payload for (etype, payload) in captured
        if etype == "validation_suite_retired"
    ]
    assert len(suite_events_second) == 0, (
        f"re-retire of already-retired suite MUST emit ZERO "
        f"validation_suite_retired events (idempotent no-op per spec §9); "
        f"got {len(suite_events_second)}. all captured: "
        f"{[e for (e, _) in captured]!r}"
    )

    # JSONL invariant: no NEW retire-phase entries written by the no-op.
    retire_traces_second = _count_validation_suite_traces()
    assert len(retire_traces_second) == len(retire_traces_first), (
        f"re-retire MUST NOT write a new JSONL trace entry; count went "
        f"from {len(retire_traces_first)} → {len(retire_traces_second)}"
    )


# ===========================================================================
# Test 16 (Cycle 3 #5) — operation_label='validation_suite_retire' on submit
# ===========================================================================


async def test_retire_uses_validation_suite_retire_op_label(
    db: AsyncSession, write_queue, monkeypatch,
):
    """Spec §4 write-queue operation labels — first ``retire`` MUST call
    ``WriteQueue.submit`` with ``operation_label='validation_suite_retire'``
    for JSONL-trace filterability.

    Per spec §4 the label appears ONLY on first-retire submissions; the
    idempotent re-retire short-circuits before ``WriteQueue.submit`` is
    invoked at all, so no operation_label is recorded on the no-op path.
    This test asserts BOTH halves of that contract: the label appears once
    on the first retire AND the second retire records zero new submit
    calls (proving the short-circuit happens before any WriteQueue work).
    """
    suite_out = await _seed_suite(db, write_queue)

    captured: list[dict] = []
    real_submit = write_queue.submit

    async def _capturing_submit(work, *, timeout=None, operation_label=None):
        captured.append(
            {"operation_label": operation_label, "timeout": timeout},
        )
        return await real_submit(
            work, timeout=timeout, operation_label=operation_label,
        )

    monkeypatch.setattr(write_queue, "submit", _capturing_submit)

    service = ValidationSuiteService()
    # First retire — expect the canonical op label on submit.
    await service.retire(
        suite_id=suite_out.id,
        reason="op-label test",
        db=db,
        write_queue=write_queue,
    )

    labels_first = [c["operation_label"] for c in captured]
    assert "validation_suite_retire" in labels_first, (
        f"first retire MUST submit with operation_label="
        f"'validation_suite_retire'; got labels={labels_first!r}"
    )

    # Reset and re-retire — short-circuit MUST happen before submit().
    captured.clear()
    await service.retire(
        suite_id=suite_out.id,
        reason="re-retire — must not submit",
        db=db,
        write_queue=write_queue,
    )
    labels_second = [c["operation_label"] for c in captured]
    assert labels_second == [], (
        f"re-retire of already-retired suite MUST short-circuit BEFORE "
        f"WriteQueue.submit (per spec §4 op-label clause); got submit "
        f"calls with labels={labels_second!r}"
    )


# ===========================================================================
# Test 17 (Cycle 3 #6) — retire raises suite_not_found on missing id
# ===========================================================================


async def test_retire_raises_suite_not_found_on_missing(
    db: AsyncSession, write_queue,
):
    """Spec §4 error envelope — retire on a non-existent ``suite_id`` raises
    ``ValueError("suite_not_found")``. Router layer (Cycle 5) translates the
    code to a 404 envelope.
    """
    service = ValidationSuiteService()
    with pytest.raises(ValueError, match="suite_not_found"):
        await service.retire(
            suite_id="not-a-real-suite-id-" + uuid.uuid4().hex,
            reason="missing suite",
            db=db,
            write_queue=write_queue,
        )


# ===========================================================================
# Test 18 (Cycle 3 #7) — get returns full ValidationSuiteOut + 404 on missing
# ===========================================================================


async def test_get_returns_full_validation_suite_out_for_existing_id(
    db: AsyncSession, write_queue,
):
    """Spec §5 get() body — ``get(suite_id)`` returns a fully-populated
    ``ValidationSuiteOut`` matching the seeded values. Missing suite raises
    ``ValueError("suite_not_found")`` (spec §4 error envelope — same code
    surfaces 404 at the REST layer).
    """
    aggregate = _canonical_aggregate(mean=7.85)
    prompt_results = _prompt_results(3)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=prompt_results,
        repo_full_name="acme/widget",
    )

    service = ValidationSuiteService()
    created = await service.create_from_run(
        run_id=run.id,
        label="get-test",
        tolerance_abs=0.5,
        db=db,
        write_queue=write_queue,
    )

    # Round-trip read of the persisted row via the service.
    fetched = await service.get(suite_id=created.id, db=db)

    assert isinstance(fetched, ValidationSuiteOut), (
        f"get() must return a Pydantic ValidationSuiteOut; got "
        f"{type(fetched)!r}"
    )
    assert fetched.id == created.id
    assert fetched.label == "get-test"
    assert fetched.tolerance_abs == 0.5
    assert fetched.source_run_id == run.id
    assert isinstance(fetched.prompts_snapshot, list)
    assert len(fetched.prompts_snapshot) == len(prompt_results)
    assert all(
        isinstance(item, PromptSnapshotItem) for item in fetched.prompts_snapshot
    ), "prompts_snapshot entries must round-trip as PromptSnapshotItem"
    assert isinstance(fetched.baseline_scores, BaselineScoresPayload)
    assert fetched.baseline_scores.mean_overall == 7.85
    # Fresh suite — retire columns must be None.
    assert fetched.retired_at is None
    assert fetched.retired_reason is None

    # Missing suite — same canonical error code as retire.
    with pytest.raises(ValueError, match="suite_not_found"):
        await service.get(
            suite_id="missing-suite-" + uuid.uuid4().hex,
            db=db,
        )


# ===========================================================================
# Test 19 (Cycle 3 #8) — list filters retired by default + pagination shape
# ===========================================================================


async def test_list_filters_retired_by_default(
    db: AsyncSession, write_queue,
):
    """Spec §4 ``GET /api/suites?include_retired=false`` — the service
    ``list()`` method's ``include_retired`` kwarg defaults to ``False``;
    retired suites are excluded by default and reappear when the caller
    opts in via ``include_retired=True``.

    Pagination envelope is the canonical ``ValidationSuiteListResponse``
    (spec §4): ``total`` counts the FILTERED set (NOT the whole DB), ``count``
    matches ``len(items)``, and ``items[i]`` are ``ValidationSuiteListItem``
    instances. The same total propagates across pages — limit/offset slice
    items without affecting total.
    """
    # Seed 5 suites, retire 2 of them.
    suites: list[ValidationSuiteOut] = []
    for i in range(5):
        suite = await _seed_suite(db, write_queue, label=f"list-fixture-{i}")
        suites.append(suite)

    service = ValidationSuiteService()
    # Retire the first two (deterministic — easier to reason about).
    for suite in suites[:2]:
        await service.retire(
            suite_id=suite.id,
            reason=f"retired in list fixture: {suite.label}",
            db=db,
            write_queue=write_queue,
        )

    # ---- Default: include_retired=False — expect 3 active suites ----
    response_default = await service.list(db=db)
    assert isinstance(response_default, ValidationSuiteListResponse), (
        f"list() must return a Pydantic ValidationSuiteListResponse; got "
        f"{type(response_default)!r}"
    )
    assert response_default.total == 3, (
        f"default list MUST exclude retired suites — expected total=3 "
        f"active out of 5 seeded; got total={response_default.total}"
    )
    assert response_default.count == 3, (
        f"count must match len(items)=3; got count={response_default.count}"
    )
    assert len(response_default.items) == 3
    assert all(
        isinstance(item, ValidationSuiteListItem)
        for item in response_default.items
    ), "items entries must be ValidationSuiteListItem"
    # No retired suites should sneak through.
    active_ids = {item.id for item in response_default.items}
    retired_ids = {suite.id for suite in suites[:2]}
    assert active_ids.isdisjoint(retired_ids), (
        f"retired suites leaked into default list — overlap: "
        f"{active_ids & retired_ids!r}"
    )

    # ---- include_retired=True — expect all 5 ----
    response_all = await service.list(db=db, include_retired=True)
    assert response_all.total == 5
    assert response_all.count == 5
    assert len(response_all.items) == 5

    # ---- Pagination — first slice ----
    response_page_1 = await service.list(db=db, limit=2, offset=0)
    # total counts the filtered set (3 active), NOT the page slice.
    assert response_page_1.total == 3, (
        f"limit/offset must NOT affect total; expected total=3, got "
        f"{response_page_1.total}"
    )
    assert len(response_page_1.items) == 2, (
        f"limit=2 page MUST contain 2 items when 3 active suites exist; "
        f"got {len(response_page_1.items)}"
    )

    # ---- Pagination — second slice ----
    response_page_2 = await service.list(db=db, limit=2, offset=2)
    assert response_page_2.total == 3
    assert len(response_page_2.items) == 1, (
        f"limit=2 offset=2 MUST contain 1 item when 3 active suites "
        f"exist; got {len(response_page_2.items)}"
    )


# ===========================================================================
# Test 20 (Cycle 3 #9) — list_replays scopes to mode='replay_run' + suite_id
# ===========================================================================


async def test_list_replays_returns_only_replay_run_mode_rows_for_suite(
    db: AsyncSession, write_queue,
):
    """Spec §5 list_replays + §4 ``GET /api/suites/{id}/replays`` — returns
    rows scoped to BOTH ``mode='replay_run'`` AND ``suite_id={suite_id}``.

    Cross-mode pollution test: ``mode='topic_probe'`` rows with the SAME
    ``suite_id`` MUST NOT appear (e.g., the source-probe run wouldn't be
    listed as a replay of the suite it spawned).

    Cross-suite pollution test: ``mode='replay_run'`` rows with a DIFFERENT
    ``suite_id`` MUST NOT appear (replays belong to their parent suite).

    Ordering: ``started_at DESC`` (newest replay first) — backed by
    ``ix_run_row_suite_id (suite_id, started_at DESC)`` per spec §3.
    """
    # Need TWO suites so the cross-suite isolation test is meaningful.
    suite_a = await _seed_suite(db, write_queue, label="suite-A")
    suite_b = await _seed_suite(db, write_queue, label="suite-B")

    # 3 replay_run rows for suite_a at distinct timestamps.
    now = datetime.now(UTC)
    replay_timestamps = [
        now.replace(microsecond=10_000),
        now.replace(microsecond=20_000),
        now.replace(microsecond=30_000),
    ]
    expected_replay_ids: list[str] = []
    for ts in replay_timestamps:
        row_id = uuid.uuid4().hex
        row = RunRow(
            id=row_id,
            mode="replay_run",
            status="completed",
            started_at=ts,
            completed_at=ts,
            prompts_generated=3,
            prompt_results=None,
            aggregate={"mean_overall": 7.5},
            suite_id=suite_a.id,
        )
        db.add(row)
        expected_replay_ids.append(row_id)

    # 2 topic_probe rows linked to suite_a (cross-mode pollution) — MUST be
    # excluded by the mode filter.
    for _ in range(2):
        row = RunRow(
            id=uuid.uuid4().hex,
            mode="topic_probe",
            status="completed",
            started_at=now,
            completed_at=now,
            prompts_generated=3,
            prompt_results=None,
            aggregate={"mean_overall": 8.0},
            suite_id=suite_a.id,
        )
        db.add(row)

    # 1 replay_run row linked to suite_b (cross-suite pollution) — MUST be
    # excluded by the suite_id filter.
    cross_suite_row_id = uuid.uuid4().hex
    db.add(
        RunRow(
            id=cross_suite_row_id,
            mode="replay_run",
            status="completed",
            started_at=now,
            completed_at=now,
            prompts_generated=3,
            prompt_results=None,
            aggregate={"mean_overall": 7.0},
            suite_id=suite_b.id,
        ),
    )

    await db.commit()

    service = ValidationSuiteService()
    response = await service.list_replays(suite_id=suite_a.id, db=db)

    # Extract the row IDs that came back. The exact return type is a
    # paginated envelope (matches GET /api/suites/{id}/replays canonical
    # contract returning ``RunListResponse`` per spec §4 line 275); attribute
    # access on .items is the canonical pagination shape.
    items = getattr(response, "items", None)
    assert items is not None, (
        f"list_replays() must return a paginated envelope with an ``items`` "
        f"attribute; got {type(response)!r}"
    )
    returned_ids = [getattr(it, "id", it.get("id") if isinstance(it, dict) else None)
                    for it in items]

    assert len(items) == 3, (
        f"list_replays MUST return exactly 3 replay_run rows scoped to "
        f"suite_a (excluding 2 topic_probe rows on same suite + 1 "
        f"replay_run on suite_b); got len={len(items)}, ids={returned_ids!r}"
    )

    # Set membership — every returned id is a known suite_a replay; the
    # cross-suite suite_b replay MUST NOT appear.
    returned_set = set(returned_ids)
    assert returned_set == set(expected_replay_ids), (
        f"returned ids must equal the 3 suite_a replay ids; expected="
        f"{set(expected_replay_ids)!r}, got={returned_set!r}"
    )
    assert cross_suite_row_id not in returned_set, (
        f"cross-suite replay leaked into list_replays(suite_a); offending "
        f"id={cross_suite_row_id!r}"
    )

    # Ordering — started_at DESC (newest first). The 3 timestamps were
    # seeded in ascending microsecond order, so the canonical desc order is
    # the reverse of insertion order.
    returned_started_ats = [
        getattr(it, "started_at", it.get("started_at") if isinstance(it, dict) else None)
        for it in items
    ]
    assert returned_started_ats == sorted(
        returned_started_ats, reverse=True,
    ), (
        f"list_replays MUST return rows ordered by started_at DESC (newest "
        f"first) per spec §3 ix_run_row_suite_id index ordering; got "
        f"{returned_started_ats!r}"
    )


# ###########################################################################
# Cycle 4 — compute_regression_alarm + 30s TTL cache + transition event — 7 tests
# ###########################################################################
#
# Plan: docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md Cycle 4 Task 4.1
# Spec: docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md
#       §4 (RegressionAlarmEntry / RegressionAlarmBlock Pydantic models)
#     + §5 (compute_regression_alarm body: alarm SQL + Python filter)
#     + §9 (regression_alarm_transition event — fires ONLY on state change)
#
# Pre-condition before Cycle 4 GREEN lands:
#   - ``ValidationSuiteService`` has no ``.compute_regression_alarm`` method →
#     ``AttributeError`` at attribute access on the service instance
#
# ---------------------------------------------------------------------------
# CONTRACT PINNED FOR GREEN
# ---------------------------------------------------------------------------
# The 30s TTL cache + state-transition map must satisfy these test seams:
#
#   1. ``compute_regression_alarm(*, db)`` returns ``RegressionAlarmBlock``.
#   2. The TTL cache is INSTANCE-SCOPED (not module-level). Two service
#      instances do NOT share cached state — every test that instantiates
#      ``ValidationSuiteService()`` starts with empty cache + empty
#      ``_prior_alarm_states`` map. Matches the spec §5 instance-local
#      ``self._prior_alarm_states`` pin (line 603).
#   3. ``self._invalidate_alarm_cache()`` is a public test-seam method that
#      clears BOTH the cached block AND any cached-at timestamp so the next
#      call re-executes the alarm SQL. Tests 6 + 7 call this between
#      consecutive ``compute_regression_alarm`` invocations to bypass the
#      30s TTL without sleeping. The implementation may name the cache
#      attributes anything — the only contract is that
#      ``_invalidate_alarm_cache()`` forces the next call to re-query.
#   4. The state-transition map keys are suite ids. Values are one of
#      ``'none' | 'nominal' | 'firing'`` per spec §9 (line 1341). The
#      ``regression_alarm_transition`` event fires ONLY when a suite's
#      new state differs from its prior state in the map.
# ---------------------------------------------------------------------------


def _firing_replay_aggregate(*, mean_overall: float) -> dict:
    """Minimal replay aggregate carrying just ``mean_overall``.

    The spec §5 Python-side filter reads
    ``replay_aggregate['mean_overall']`` and compares against
    ``baseline_scores['mean_overall'] - tolerance_abs``. Other aggregate
    keys do not influence the alarm computation.
    """
    return {"mean_overall": mean_overall}


async def _insert_replay_run(
    db: AsyncSession,
    *,
    suite_id: str,
    mean_overall: float,
    started_at: datetime | None = None,
) -> RunRow:
    """Insert a ``replay_run`` RunRow scoped to ``suite_id``.

    Shared by every Cycle 4 test that needs the alarm SQL JOIN to find
    completed replay rows. Default ``started_at`` is ``datetime.now(UTC)``.
    """
    started = started_at or datetime.now(UTC)
    row = RunRow(
        id=uuid.uuid4().hex,
        mode="replay_run",
        status="completed",
        started_at=started,
        completed_at=started,
        prompts_generated=3,
        prompt_results=None,
        aggregate=_firing_replay_aggregate(mean_overall=mean_overall),
        suite_id=suite_id,
    )
    db.add(row)
    await db.commit()
    return row


# ===========================================================================
# Test 21 (Cycle 4 #1) — empty block when there are no replays
# ===========================================================================


async def test_compute_regression_alarm_returns_empty_block_with_no_replays(
    db: AsyncSession, write_queue,
):
    """Spec §5 alarm SQL — ``suites_total`` counts ACTIVE suites; suites
    without any completed replays are excluded by the JOIN, so
    ``latest_alarms`` is empty and ``suites_in_alarm == 0``.

    The spec §4 ``RegressionAlarmBlock`` shape requires ``suites_total``,
    ``suites_in_alarm``, ``latest_alarms`` — this test pins all three on the
    zero-replay path.
    """
    from app.schemas.validation_suite import RegressionAlarmBlock

    # Seed 3 active suites — no replays inserted.
    for i in range(3):
        await _seed_suite(db, write_queue, label=f"empty-replay-{i}")

    service = ValidationSuiteService()
    block = await service.compute_regression_alarm(db=db)

    assert isinstance(block, RegressionAlarmBlock), (
        f"compute_regression_alarm must return a RegressionAlarmBlock; got "
        f"{type(block)!r}"
    )
    assert block.suites_total == 3, (
        f"suites_total counts ACTIVE suites (3 seeded, 0 retired); got "
        f"{block.suites_total}"
    )
    assert block.suites_in_alarm == 0, (
        f"suites_in_alarm must be 0 when no replays exist for any suite "
        f"(spec §5 alarm JOIN excludes suites without completed replays); "
        f"got {block.suites_in_alarm}"
    )
    assert block.latest_alarms == [], (
        f"latest_alarms must be empty when no replays exist; got "
        f"{block.latest_alarms!r}"
    )


# ===========================================================================
# Test 22 (Cycle 4 #2) — retired suites excluded from the alarm SQL JOIN
# ===========================================================================


async def test_compute_regression_alarm_excludes_retired_suites(
    db: AsyncSession, write_queue,
):
    """Spec §5 alarm SQL — ``WHERE s.retired_at IS NULL`` excludes retired
    suites entirely. ``suites_total`` reflects ACTIVE suites only, and any
    replay attached to a retired suite must not surface as an alarm.

    Setup: 5 suites with firing replays each → retire 2 → expect 3 in total
    and 3 in alarm.
    """
    # 5 suites with baseline mean=7.85, tolerance_abs=0.5. Each gets a
    # firing replay so the alarm SQL would flag every one if not for the
    # retired_at filter.
    suites: list[ValidationSuiteOut] = []
    for i in range(5):
        suite = await _seed_suite(
            db, write_queue, label=f"retire-fixture-{i}", tolerance_abs=0.5,
        )
        suites.append(suite)
        # Firing replay: mean=7.20, delta=-0.65 (> 0.5 tolerance → fires)
        await _insert_replay_run(
            db, suite_id=suite.id, mean_overall=7.20,
        )

    service = ValidationSuiteService()
    # Retire the first 2 suites — those replays MUST NOT appear in alarms.
    for suite in suites[:2]:
        await service.retire(
            suite_id=suite.id,
            reason="retired before alarm check",
            db=db,
            write_queue=write_queue,
        )

    await db.commit()  # ensure retire writes visible to the alarm SQL

    block = await service.compute_regression_alarm(db=db)

    assert block.suites_total == 3, (
        f"suites_total must exclude retired suites (5 seeded, 2 retired = "
        f"3 active); got {block.suites_total}"
    )
    assert block.suites_in_alarm == 3, (
        f"all 3 active suites have firing replays — alarm SQL must report "
        f"3 in alarm (retired suites with firing replays excluded); got "
        f"{block.suites_in_alarm}"
    )
    assert len(block.latest_alarms) == 3, (
        f"latest_alarms must contain 3 entries (1 per active firing "
        f"suite); got {len(block.latest_alarms)}"
    )

    # Retired suite ids must NOT appear in any alarm entry.
    retired_ids = {s.id for s in suites[:2]}
    alarm_ids = {entry.suite_id for entry in block.latest_alarms}
    assert alarm_ids.isdisjoint(retired_ids), (
        f"retired suites leaked into alarm output — overlap: "
        f"{alarm_ids & retired_ids!r}"
    )


# ===========================================================================
# Test 23 (Cycle 4 #3) — MAX(started_at) selects the latest replay per suite
# ===========================================================================


async def test_compute_regression_alarm_uses_latest_replay_per_suite(
    db: AsyncSession, write_queue,
):
    """Spec §5 alarm SQL ``MAX(started_at) AS max_started`` per-suite — when
    multiple replays exist, the alarm reads the LATEST one regardless of
    whether older replays would fire. Pins the per-suite MAX() semantics.

    Setup: 1 suite (baseline 7.85, tolerance 0.5) + 3 replays at distinct
    timestamps:
      * oldest mean=7.50 → delta=-0.35 → within tolerance (would NOT fire)
      * middle mean=7.21 → delta=-0.64 → would fire
      * newest mean=7.10 → delta=-0.75 → would fire — this is the one
        MAX(started_at) selects

    The latest_alarms entry MUST carry the NEWEST replay's mean + delta.
    """
    suite_out = await _seed_suite(
        db, write_queue, label="max-started", tolerance_abs=0.5,
    )

    base = datetime.now(UTC)
    # Insert in NON-chronological order to defend against accidental
    # insertion-order dependence in the GREEN implementation.
    await _insert_replay_run(  # middle (would fire, but NOT the latest)
        db, suite_id=suite_out.id, mean_overall=7.21,
        started_at=base.replace(microsecond=20_000),
    )
    await _insert_replay_run(  # newest — this is the MAX(started_at) row
        db, suite_id=suite_out.id, mean_overall=7.10,
        started_at=base.replace(microsecond=30_000),
    )
    await _insert_replay_run(  # oldest (within tolerance — wouldn't fire)
        db, suite_id=suite_out.id, mean_overall=7.50,
        started_at=base.replace(microsecond=10_000),
    )

    service = ValidationSuiteService()
    block = await service.compute_regression_alarm(db=db)

    assert len(block.latest_alarms) == 1, (
        f"exactly 1 alarm entry expected (1 suite, latest replay fires); "
        f"got {len(block.latest_alarms)} entries"
    )
    entry = block.latest_alarms[0]
    assert entry.latest_mean == 7.10, (
        f"latest_mean MUST equal the MAX(started_at) replay's "
        f"mean_overall (newest=7.10), NOT the middle replay's 7.21 nor "
        f"the oldest 7.50; got latest_mean={entry.latest_mean!r}"
    )
    # delta = latest_mean - baseline_mean = 7.10 - 7.85 = -0.75
    assert abs(entry.delta_abs - (-0.75)) < 1e-9, (
        f"delta_abs MUST equal (newest replay mean - baseline mean) = "
        f"-0.75 within float epsilon; got delta_abs={entry.delta_abs!r}"
    )


# ===========================================================================
# Test 24 (Cycle 4 #4) — alarm fires when delta exceeds tolerance
# ===========================================================================


async def test_compute_regression_alarm_fires_when_delta_exceeds_tolerance(
    db: AsyncSession, write_queue,
):
    """Spec §5 Python-side filter — fires when
    ``replay_aggregate['mean_overall'] <
    baseline_scores['mean_overall'] - tolerance_abs``.

    Setup: baseline=7.85, tolerance=0.5, latest replay mean=7.21
        → 7.21 < 7.85 - 0.5 = 7.35
        → fires with delta_abs = 7.21 - 7.85 = -0.64

    Every populated ``RegressionAlarmEntry`` field is asserted column-by-
    column per spec §10 Cycle 4 INTEGRATE A1 (alarm-query result columns
    ALL consumed — none silently dropped).
    """
    suite_out = await _seed_suite(
        db, write_queue, label="fires-fixture", tolerance_abs=0.5,
    )

    replay_started = datetime.now(UTC).replace(microsecond=42_000)
    replay = await _insert_replay_run(
        db, suite_id=suite_out.id, mean_overall=7.21,
        started_at=replay_started,
    )

    service = ValidationSuiteService()
    block = await service.compute_regression_alarm(db=db)

    assert block.suites_total == 1
    assert block.suites_in_alarm == 1
    assert len(block.latest_alarms) == 1

    entry = block.latest_alarms[0]
    assert entry.suite_id == suite_out.id, (
        f"alarm entry suite_id must match seeded suite; expected="
        f"{suite_out.id!r}, got={entry.suite_id!r}"
    )
    assert entry.label == "fires-fixture", (
        f"alarm entry must carry the suite's label; expected="
        f"'fires-fixture', got={entry.label!r}"
    )
    assert entry.baseline_mean == 7.85, (
        f"baseline_mean must equal the suite's baseline_scores.mean_overall "
        f"(7.85 per _canonical_aggregate default); got "
        f"{entry.baseline_mean!r}"
    )
    assert entry.latest_mean == 7.21, (
        f"latest_mean must equal replay aggregate.mean_overall (7.21); "
        f"got {entry.latest_mean!r}"
    )
    assert abs(entry.delta_abs - (-0.64)) < 1e-9, (
        f"delta_abs must equal (replay_mean - baseline_mean) = -0.64 "
        f"within float epsilon; got {entry.delta_abs!r}"
    )
    assert entry.tolerance_abs == 0.5, (
        f"tolerance_abs must round-trip from suite column; got "
        f"{entry.tolerance_abs!r}"
    )
    assert entry.latest_replay_id == replay.id, (
        f"latest_replay_id must equal RunRow.id of the MAX(started_at) "
        f"replay; expected={replay.id!r}, got={entry.latest_replay_id!r}"
    )
    # latest_replay_at is timezone-aware UTC from the seeded RunRow.
    assert entry.latest_replay_at == replay_started, (
        f"latest_replay_at must equal the replay RunRow's started_at; "
        f"expected={replay_started!r}, got={entry.latest_replay_at!r}"
    )


# ===========================================================================
# Test 25 (Cycle 4 #5) — does NOT fire within tolerance
# ===========================================================================


async def test_compute_regression_alarm_does_not_fire_within_tolerance(
    db: AsyncSession, write_queue,
):
    """Spec §5 Python-side filter — when ``|delta| <= tolerance_abs`` the
    suite is NOMINAL, not firing. The suite still counts in ``suites_total``
    (active suite) but ``suites_in_alarm == 0`` and ``latest_alarms`` is
    empty.

    Setup: baseline=7.85, tolerance=0.5, latest replay mean=7.50
        → 7.50 < 7.85 - 0.5 = 7.35 is FALSE
        → does NOT fire
    """
    suite_out = await _seed_suite(
        db, write_queue, label="within-tolerance", tolerance_abs=0.5,
    )
    await _insert_replay_run(
        db, suite_id=suite_out.id, mean_overall=7.50,
    )

    service = ValidationSuiteService()
    block = await service.compute_regression_alarm(db=db)

    assert block.suites_total == 1, (
        f"active suite must still appear in suites_total even when not "
        f"firing; got {block.suites_total}"
    )
    assert block.suites_in_alarm == 0, (
        f"replay within tolerance (delta=-0.35, tolerance=0.5) must NOT "
        f"fire; got suites_in_alarm={block.suites_in_alarm}"
    )
    assert block.latest_alarms == [], (
        f"latest_alarms must be empty when no replay exceeds tolerance; "
        f"got {block.latest_alarms!r}"
    )


# ===========================================================================
# Test 26 (Cycle 4 #6) — 30s TTL cache hits on the second call
# ===========================================================================


async def test_compute_regression_alarm_30s_ttl_cache_hits_on_second_call(
    db: AsyncSession, write_queue,
):
    """Spec §4 alarm health block — 30s ``TTLCache`` (matches
    ``taxonomy/sub_domain_readiness.py`` pattern). Two consecutive calls
    within the TTL window MUST execute the alarm SQL once, not twice.

    Cache-bypass mechanism pinned for GREEN
    --------------------------------------
    The TTL cache lives on the SERVICE INSTANCE (not module-level). The
    service must expose ``self._invalidate_alarm_cache()`` as a public
    test seam — calling it forces the next ``compute_regression_alarm``
    invocation to re-query the DB. Test 7 below uses this seam to
    advance state without sleeping past TTL.

    Measurement: intercept ``AsyncSession.execute`` on the read session
    and count alarm-SQL invocations. The alarm SQL contains the literal
    string ``validation_suite`` AND ``run_row`` in its ``FROM`` clause —
    we use that compound signature to disambiguate from any other
    incidental query the service may run.
    """
    suite_out = await _seed_suite(
        db, write_queue, label="cache-fixture", tolerance_abs=0.5,
    )
    await _insert_replay_run(
        db, suite_id=suite_out.id, mean_overall=7.20,
    )

    service = ValidationSuiteService()

    # Count alarm-SQL executions by intercepting the read session's
    # async_session_factory. The factory builds a fresh AsyncSession on
    # each ``compute_regression_alarm`` call (matches the existing service
    # pattern); we wrap each session's ``.execute`` so cache hits (which
    # do NOT open a session) record zero executions.
    import app.database as database_mod
    real_factory = database_mod.async_session_factory
    sql_execute_count = {"alarm_sql": 0}

    def _wrap_factory(*args, **kwargs):
        ctx = real_factory(*args, **kwargs)
        real_aenter = ctx.__aenter__

        async def _aenter_wrapper(*a, **k):
            session = await real_aenter(*a, **k)
            real_execute = session.execute

            async def _counting_execute(stmt, *e_args, **e_kwargs):
                try:
                    rendered = str(stmt)
                except (TypeError, ValueError):
                    rendered = ""
                # The alarm SQL joins validation_suite with run_row —
                # count any statement referencing BOTH tables as an
                # alarm-SQL execution. Cycle 2-3 reads touch only one
                # table at a time so they will not be miscounted.
                if "validation_suite" in rendered and "run_row" in rendered:
                    sql_execute_count["alarm_sql"] += 1
                return await real_execute(stmt, *e_args, **e_kwargs)

            session.execute = _counting_execute  # type: ignore[method-assign]
            return session

        ctx.__aenter__ = _aenter_wrapper
        return ctx

    monkeypatch_target = _wrap_factory

    import contextlib
    @contextlib.contextmanager
    def _patched():
        original = database_mod.async_session_factory
        database_mod.async_session_factory = monkeypatch_target  # type: ignore[assignment]
        try:
            yield
        finally:
            database_mod.async_session_factory = original

    with _patched():
        # First call — cache miss, MUST hit the DB exactly once.
        block_1 = await service.compute_regression_alarm(db=db)
        first_call_count = sql_execute_count["alarm_sql"]
        assert first_call_count >= 1, (
            f"first compute_regression_alarm call MUST execute the alarm "
            f"SQL at least once (cache miss); got "
            f"alarm_sql_executions={first_call_count}"
        )

        # Second call within TTL — cache HIT, MUST NOT re-execute SQL.
        block_2 = await service.compute_regression_alarm(db=db)
        second_call_count = sql_execute_count["alarm_sql"]
        assert second_call_count == first_call_count, (
            f"30s TTL cache violated — second call within window re-"
            f"executed the alarm SQL. Expected total executions to stay "
            f"at {first_call_count}; got {second_call_count}. The cache "
            f"must short-circuit the second call before opening a read "
            f"session. (Cache-bypass seam test: see "
            f"``self._invalidate_alarm_cache()`` contract.)"
        )

    # Block returned from the cache must be equivalent to the original.
    assert block_2.suites_total == block_1.suites_total
    assert block_2.suites_in_alarm == block_1.suites_in_alarm
    assert len(block_2.latest_alarms) == len(block_1.latest_alarms)


# ===========================================================================
# Test 27 (Cycle 4 #7) — regression_alarm_transition event on state change only
# ===========================================================================


async def test_regression_alarm_transition_event_fires_on_state_change_only(
    db: AsyncSession, write_queue, monkeypatch,
):
    """Spec §9 — ``regression_alarm_transition`` event fires AFTER each
    alarm-query run, ONLY when a suite's state CHANGES
    (``none → firing``, ``firing → nominal``, etc.). Steady-state calls
    (no transition) emit ZERO events.

    Prior-state map lives in-memory on the service instance per spec §9
    (``self._prior_alarm_states: dict[str, Literal['nominal','firing','none']]``).
    Single instance is used across all four calls below so the map
    survives between invocations.

    Cache-bypass between calls uses the contract pinned in Test 6 —
    ``service._invalidate_alarm_cache()`` forces the next call to re-
    query without sleeping past the 30s TTL.

    Four-phase contract:
      Phase A — no replays, fresh service → seeds prior states as 'none'
                (or 'nominal' if implementation treats no-replay as
                nominal — accept either initial state).
      Phase B — insert firing replay, invalidate cache, recompute →
                state transitions to 'firing' → ONE event emitted.
      Phase C — recompute without state change → ZERO new events.
      Phase D — insert a within-tolerance replay (newer started_at than
                the firing one), invalidate cache, recompute → state
                transitions 'firing' → 'nominal' → ONE event emitted.
    """
    suite_out = await _seed_suite(
        db, write_queue, label="transition-fixture", tolerance_abs=0.5,
    )

    # Capture event_bus.publish calls — filter to
    # ``regression_alarm_transition`` event type.
    from app.services.event_bus import event_bus
    captured: list[tuple[str, dict]] = []
    real_publish = event_bus.publish

    def _capturing_publish(event_type, data):
        captured.append(
            (event_type, dict(data) if isinstance(data, dict) else {}),
        )
        return real_publish(event_type, data)

    monkeypatch.setattr(event_bus, "publish", _capturing_publish)

    def _transitions_since_reset() -> list[dict]:
        return [
            payload for (etype, payload) in captured
            if etype == "regression_alarm_transition"
        ]

    service = ValidationSuiteService()

    # ---- Phase A: no replays → seeds prior_alarm_states ----
    await service.compute_regression_alarm(db=db)
    # No transition event expected — there's no PRIOR state to transition
    # FROM on a fresh instance with no prior calls. Some implementations
    # may emit a 'none → nominal' synthetic event here; we don't assert
    # zero on Phase A to leave room for that variation. The CORE contract
    # is asserted in Phases B-D below.

    captured.clear()

    # ---- Phase B: insert firing replay → state transitions to 'firing' ----
    await _insert_replay_run(
        db, suite_id=suite_out.id, mean_overall=7.10,
        started_at=datetime.now(UTC).replace(microsecond=10_000),
    )
    service._invalidate_alarm_cache()  # bypass 30s TTL test seam
    await service.compute_regression_alarm(db=db)

    phase_b_transitions = _transitions_since_reset()
    assert len(phase_b_transitions) == 1, (
        f"Phase B — first firing replay must emit exactly 1 "
        f"regression_alarm_transition event; got "
        f"{len(phase_b_transitions)}. all captured types: "
        f"{[e for (e, _) in captured]!r}"
    )
    payload_b = phase_b_transitions[0]
    assert payload_b.get("suite_id") == suite_out.id
    assert payload_b.get("previous_state") in ("none", "nominal"), (
        f"Phase B — previous_state must be 'none' or 'nominal' before the "
        f"first firing replay; got {payload_b.get('previous_state')!r}"
    )
    assert payload_b.get("new_state") == "firing", (
        f"Phase B — new_state must be 'firing' after the firing replay "
        f"is detected; got {payload_b.get('new_state')!r}"
    )

    captured.clear()

    # ---- Phase C: recompute with same firing state → ZERO events ----
    service._invalidate_alarm_cache()  # bypass 30s TTL test seam
    await service.compute_regression_alarm(db=db)
    phase_c_transitions = _transitions_since_reset()
    assert len(phase_c_transitions) == 0, (
        f"Phase C — re-computing with no state change must emit ZERO "
        f"regression_alarm_transition events (state-change-only "
        f"semantics per spec §9 line 1341); got "
        f"{len(phase_c_transitions)} events: {phase_c_transitions!r}"
    )

    # ---- Phase D: insert within-tolerance replay → transitions to nominal ----
    # Newer started_at than the firing replay so MAX(started_at) picks it up.
    await _insert_replay_run(
        db, suite_id=suite_out.id, mean_overall=7.60,
        started_at=datetime.now(UTC).replace(microsecond=80_000),
    )
    service._invalidate_alarm_cache()  # bypass 30s TTL test seam
    await service.compute_regression_alarm(db=db)

    phase_d_transitions = _transitions_since_reset()
    assert len(phase_d_transitions) == 1, (
        f"Phase D — recovery (firing → nominal) must emit exactly 1 "
        f"regression_alarm_transition event; got "
        f"{len(phase_d_transitions)}: {phase_d_transitions!r}"
    )
    payload_d = phase_d_transitions[0]
    assert payload_d.get("suite_id") == suite_out.id
    assert payload_d.get("previous_state") == "firing", (
        f"Phase D — previous_state must be 'firing' (preserved from "
        f"Phase B's transition); got {payload_d.get('previous_state')!r}"
    )
    assert payload_d.get("new_state") == "nominal", (
        f"Phase D — new_state must be 'nominal' after the within-"
        f"tolerance replay overrides the prior firing one; got "
        f"{payload_d.get('new_state')!r}"
    )
