"""Cycle 2 RED tests for ``ValidationSuiteService.create_from_run`` — 11 tests.

Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 2 Task 2.1
Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4 + §5

These tests are deliberately authored BEFORE the implementation exists. They
import from ``app.services.validation_suite_service`` and
``app.schemas.validation_suite`` — neither module exists yet, so every test
fails at import time with ``ModuleNotFoundError``. That is correct RED
behaviour for the strict TDD protocol (`feedback_tdd_protocol.md`).

Per spec §5 the service is detached-ORM-safe (Foundation P4 contract): reads
happen inside a short DB session; the snapshot dataclass crosses the
``WriteQueue.submit()`` boundary; no DB session is held during persistence.
Per spec §9 trace tagging suite creates emit JSONL with
``phase="validation_suite"``.

Cycle 1 already landed:
  * `ValidationSuite` ORM declaration (`app/models.py:675`)
  * Alembic migration `5576c539720f` creating the table + FK + indexes
  * `RunRow.suite_id` FK + `ix_run_row_suite_id` index

Cycle 2 must add:
  * NEW `app/schemas/validation_suite.py` (Pydantic schemas per spec §4)
  * NEW `app/services/validation_suite_service.py` (`ValidationSuiteService` +
    `SuiteSnapshotInputs` per spec §5)
  * Write queue operation label ``"validation_suite_create"``
  * Event ``validation_suite_created`` emitted post-submit success
  * JSONL trace entry with ``phase="validation_suite"``
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
from app.schemas.validation_suite import (  # noqa: F401 — import failure is the RED signal
    BaselineScoresPayload,
    PerPromptScore,
    PromptSnapshotItem,
    ValidationSuiteOut,
)
from app.services.validation_suite_service import (  # noqa: F401 — import failure is the RED signal
    SuiteSnapshotInputs,
    ValidationSuiteService,
)

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
