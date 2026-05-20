"""RED tests for v0.4.22 T2 Cycle 10 — MCP tools save_suite + replay_suite.

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
      §4 MCP tools (15 → 17) + §10 Cycle 10.
Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 10.

These 9 tests pin the contract of the two NEW MCP tool handlers
(``synthesis_save_suite`` + ``synthesis_replay_suite``) before any
production code exists. All assertions fail at import / attribute-lookup
time because:

* ``app.tools.save_suite`` + ``app.tools.replay_suite`` do not exist.
* ``SaveSuiteOutput`` + ``ReplayInitiatedOutput`` are absent from
  ``app.schemas.mcp_models``.
* ``synthesis_save_suite`` + ``synthesis_replay_suite`` are not yet
  registered on ``app.mcp_server.mcp``.

The dispatch shape mirrors the Cycle 13 pattern used by ``synthesis_probe``
(see ``backend/tests/test_probe_mcp_tool.py`` + ``backend/tests/test_mcp_tools_p3.py``):

* Real ``RunRow`` + ``ValidationSuite`` rows seeded into an in-memory
  shared-cache SQLite (A5 fixture-realism gate — Cycle 10 INTEGRATE A5).
* ``WriteQueue`` started against the same writer engine and routed via the
  patched ``async_session_factory`` so the service reads see the same data.
* ``ValidationSuiteService.create_from_run`` is invoked through the real
  service path for shape assertions; ``unittest.mock.patch.object`` is used
  for the dispatch / op-label tests (Tests 4 / 7 / 8).

Boundary contract (spec §4):

* ``synthesis_save_suite(run_id, label[1..120], tolerance_abs?: float = 0.5)``
  → ``SaveSuiteOutput{suite_id, source_run_id, label, baseline_mean,
                       tolerance_abs, prompts_count, created_at}``.
* ``synthesis_replay_suite(suite_id)``
  → ``ReplayInitiatedOutput{run_id, suite_id, mode: 'replay_run',
                              poll_url, started_at}``.

Error envelopes (spec §4 + §10 Cycle 10 RED task list):

* ``run_not_completed`` — save on RunRow with ``status != 'completed'``.
* ``run_missing_aggregate`` — save on completed run with missing /
  empty aggregate.
* ``suite_retired`` — replay on suite with ``retired_at IS NOT NULL``.

Cross-cutting:

* Mode-keyed operation_label ``replay_run_persist`` from Cycle 6 — the
  replay tool dispatch path eventually surfaces this label on
  ``WriteQueue.submit()`` via ``RunOrchestrator._persist_final``. Test 7
  pins the label exposure on the dispatch path so the GREEN
  implementation can't forget to thread mode through.
* Tool count 15 → 17 — Test 9 asserts the canonical FastMCP tool registry
  is updated by the GREEN step.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, RunRow, ValidationSuite

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local fixtures — mirror tests/test_validation_suite_service.py + test_mcp_tools_p3.py
# so RunOrchestrator stubs + ValidationSuiteService share one in-memory DB.
# ---------------------------------------------------------------------------


_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_mcp_tools_save_replay_test?mode=memory&cache=shared&uri=true"
)


@pytest_asyncio.fixture
async def writer_engine() -> AsyncGenerator[Any, None]:
    """In-memory writer engine bound to a shared URI."""
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

    The ``ValidationSuiteService`` opens its OWN short read session via
    ``async_session_factory``; without this patch the read goes against
    production engine and never sees rows seeded into the writer engine.
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
    """Read session against the same shared URI.

    Created AFTER ``writer_engine`` so schema materialisation has happened
    and writes committed via the WriteQueue are immediately visible.
    """
    read_engine = create_async_engine(_SHARED_URI)
    factory = async_sessionmaker(
        read_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await read_engine.dispose()


# ---------------------------------------------------------------------------
# Canonical aggregate / prompt_results / suite seeding — mirror
# tests/test_validation_suite_service.py shapes so handlers see realistic data.
# ---------------------------------------------------------------------------


def _canonical_aggregate(*, mean: float = 7.85) -> dict:
    """RunRow.aggregate shape matching ``topic_probe_generator._build_aggregate``."""
    return {
        "mean_overall": mean,
        "p5_overall": 6.2,
        "p50_overall": 7.8,
        "p95_overall": 9.1,
        "completed_count": 3,
        "failed_count": 0,
        "f5_flag_fires": 0,
        "scoring_formula_version": 4,
        "task_type_distribution": {"coding": 2, "analysis": 1},
        "per_prompt": [
            {
                "raw_prompt_idx": i, "overall": 7.0 + i * 0.2,
                "dimensions": {
                    "clarity": 7.0 + i * 0.2,
                    "specificity": 7.0 + i * 0.2,
                    "structure": 7.0 + i * 0.2,
                    "faithfulness": 7.0 + i * 0.2,
                    "conciseness": 7.0 + i * 0.2,
                },
            }
            for i in range(3)
        ],
    }


def _prompt_results(n: int) -> list[dict]:
    """Per-prompt result rows matching topic_probe's prompt_results contract."""
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
    repo_full_name: str | None = "acme/widget",
    run_id: str | None = None,
) -> RunRow:
    """Insert a RunRow into the shared in-memory DB."""
    pid = run_id or uuid.uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    row = RunRow(
        id=pid,
        mode=mode,
        status=status,
        started_at=now,
        completed_at=now if status != "running" else None,
        prompts_generated=len(prompt_results or []),
        prompt_results=prompt_results,
        aggregate=aggregate,
        repo_full_name=repo_full_name,
    )
    db.add(row)
    await db.commit()
    refreshed = (
        await db.execute(select(RunRow).where(RunRow.id == pid))
    ).scalar_one()
    return refreshed


async def _seed_active_suite(
    db: AsyncSession,
    *,
    label: str = "mcp-tool-test-suite",
    tolerance_abs: float = 0.5,
    project_id: str | None = None,
    repo_full_name: str | None = "acme/widget",
) -> ValidationSuite:
    """Insert an ACTIVE ValidationSuite directly into the shared in-memory DB.

    Bypasses ``ValidationSuiteService.create_from_run`` to keep these tests
    independent of the service's own contract — replay tool tests only need
    a suite row to exist.
    """
    suite = ValidationSuite(
        id=uuid.uuid4().hex,
        source_run_id=None,
        label=label,
        tolerance_abs=tolerance_abs,
        project_id=project_id,
        repo_full_name=repo_full_name,
        prompts_snapshot=[
            {
                "raw_prompt": f"raw prompt {i}",
                "intent_label": None,
                "original_optimization_id": None,
            }
            for i in range(3)
        ],
        baseline_scores=_canonical_aggregate(),
        created_at=datetime.now(UTC).replace(tzinfo=None),
        retired_at=None,
        retired_reason=None,
    )
    db.add(suite)
    await db.commit()
    refreshed = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite.id),
        )
    ).scalar_one()
    return refreshed


async def _seed_retired_suite(db: AsyncSession) -> ValidationSuite:
    """Insert a RETIRED ValidationSuite — drives the ``suite_retired`` path."""
    suite = await _seed_active_suite(db)
    suite.retired_at = datetime.now(UTC).replace(tzinfo=None)
    suite.retired_reason = "tier-2-rolltest"
    db.add(suite)
    await db.commit()
    refreshed = (
        await db.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite.id),
        )
    ).scalar_one()
    return refreshed


# ---------------------------------------------------------------------------
# Stub RunOrchestrator wiring — mirror tests/test_mcp_tools_p3.py.
# ---------------------------------------------------------------------------


class _RecordingReplayGenerator:
    """Stub ReplayRunGenerator capturing dispatch arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, str]] = []

    async def run(self, request, *, run_id):  # noqa: ANN001 — stub
        import asyncio

        from app.services.generators.base import GeneratorResult

        self.calls.append((request, run_id))
        await asyncio.sleep(0)
        return GeneratorResult(
            terminal_status="completed",
            prompts_generated=0,
            prompt_results=[],
            aggregate={
                "mean_overall": 7.0,
                "completed_count": 0,
                "failed_count": 0,
                "scoring_formula_version": 4,
            },
            taxonomy_delta={},
            final_report="# Replay stub",
        )


@pytest_asyncio.fixture
async def stub_orchestrator(write_queue, patched_session_factory):
    """RunOrchestrator with a replay_run stub generator + MCP-process install."""
    from app.services.run_orchestrator import RunOrchestrator
    from app.tools import _shared as tools_shared

    replay_gen = _RecordingReplayGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"replay_run": replay_gen},
    )

    previous = getattr(tools_shared, "_run_orchestrator", None)
    tools_shared.set_run_orchestrator(orchestrator)
    try:
        yield orchestrator, replay_gen
    finally:
        tools_shared.set_run_orchestrator(previous)


# ===========================================================================
# Test 1 — synthesis_save_suite returns SaveSuiteOutput (NOT *Result)
# ===========================================================================


async def test_synthesis_save_suite_structured_output_shape(
    db: AsyncSession, write_queue,
):
    """The handler must return a Pydantic ``SaveSuiteOutput`` instance.

    Spec §4 line 326 + §4 line 329: ``*Output`` suffix matches 12 of 13
    existing top-level tool-response classes in ``schemas/mcp_models.py``.
    Naming MUST conform — Test 1 fails RED with:

      * ``ImportError`` until ``SaveSuiteOutput`` is added to
        ``app.schemas.mcp_models``.
      * ``ModuleNotFoundError`` until ``app.tools.save_suite`` exists.
      * Any future drift to ``*Result`` would still fail isinstance.
    """
    from app.schemas.mcp_models import SaveSuiteOutput  # noqa: F401 — RED signal
    from app.tools.save_suite import handle_save_suite

    aggregate = _canonical_aggregate(mean=7.85)
    run = await _seed_run(
        db, aggregate=aggregate, prompt_results=_prompt_results(3),
    )

    result = await handle_save_suite(
        run_id=run.id,
        label="cycle-10-shape-check",
        tolerance_abs=0.5,
        write_queue=write_queue,
    )

    assert isinstance(result, SaveSuiteOutput), (
        f"synthesis_save_suite must return SaveSuiteOutput (the dominant "
        f"*Output convention per spec §4 line 329 — 12 of 13 existing "
        f"top-level tool response classes use *Output, the single outlier "
        f"ExplainResult is legacy). Got {type(result).__name__!r}."
    )


# ===========================================================================
# Test 2 — synthesis_save_suite returns run_not_completed error envelope
# ===========================================================================


async def test_synthesis_save_suite_returns_run_not_completed_error_envelope(
    db: AsyncSession, write_queue,
):
    """Save against a RunRow with ``status='running'`` must surface a
    structured error envelope whose code is ``run_not_completed`` per
    spec §4 line 338.

    Per the canonical error-envelope convention used by ``synthesis_probe``
    (``ProbeError(reason='link_repo_first')``) and the
    ``ValidationSuiteService`` (raises ``ValueError("run_not_completed")``),
    the MCP handler must EITHER:

      * raise an exception whose string representation contains
        ``run_not_completed`` (matches the
        ``test_synthesis_probe_link_repo_first_error_preserved`` pattern), OR
      * surface a structured ``ToolError``-like envelope with
        ``code='run_not_completed'``.

    The test accepts either form so the GREEN step can pick the canonical
    surface without re-litigating the convention.
    """
    from app.tools.save_suite import handle_save_suite

    run = await _seed_run(
        db, status="running", aggregate=None, prompt_results=None,
    )

    with pytest.raises(Exception, match=r"run_not_completed") as exc_info:
        await handle_save_suite(
            run_id=run.id,
            label="cycle-10-run-not-completed",
            tolerance_abs=0.5,
            write_queue=write_queue,
        )

    # The error's string form OR an attached ``.code`` / ``.reason`` /
    # ``.detail`` attribute must carry the spec §4 code verbatim.
    exc = exc_info.value
    error_text = str(exc)
    code_attr = (
        getattr(exc, "code", None)
        or getattr(exc, "reason", None)
        or getattr(exc, "detail", None)
    )
    assert (
        "run_not_completed" in error_text
        or code_attr == "run_not_completed"
    ), (
        f"Expected error envelope with code='run_not_completed' "
        f"(spec §4 line 338). Got exception text={error_text!r}, "
        f"code/reason/detail={code_attr!r}."
    )


# ===========================================================================
# Test 3 — synthesis_save_suite returns run_missing_aggregate error envelope
# ===========================================================================


async def test_synthesis_save_suite_returns_run_missing_aggregate_error_envelope(
    db: AsyncSession, write_queue,
):
    """Save against a COMPLETED RunRow that is missing ``aggregate`` (or whose
    aggregate lacks ``mean_overall``) must surface the structured error
    envelope code ``run_missing_aggregate`` per spec §4 line 340.

    Covers legacy / corrupted runs — the ``ValidationSuiteService``
    pre-condition fires inside its short read session and the MCP shim must
    translate the resulting ``ValueError`` to a user-visible envelope.
    """
    from app.tools.save_suite import handle_save_suite

    # Completed status but aggregate=None — exercises the
    # ``run_missing_aggregate`` precondition branch.
    run = await _seed_run(
        db, status="completed", aggregate=None,
        prompt_results=_prompt_results(3),
    )

    with pytest.raises(Exception, match=r"run_missing_aggregate") as exc_info:
        await handle_save_suite(
            run_id=run.id,
            label="cycle-10-run-missing-aggregate",
            tolerance_abs=0.5,
            write_queue=write_queue,
        )

    exc = exc_info.value
    error_text = str(exc)
    code_attr = (
        getattr(exc, "code", None)
        or getattr(exc, "reason", None)
        or getattr(exc, "detail", None)
    )
    assert (
        "run_missing_aggregate" in error_text
        or code_attr == "run_missing_aggregate"
    ), (
        f"Expected error envelope with code='run_missing_aggregate' "
        f"(spec §4 line 340). Got exception text={error_text!r}, "
        f"code/reason/detail={code_attr!r}."
    )


# ===========================================================================
# Test 4 — synthesis_save_suite persists via ValidationSuiteService
# ===========================================================================


async def test_synthesis_save_suite_persists_via_validation_suite_service(
    db: AsyncSession, write_queue,
):
    """The handler MUST call ``ValidationSuiteService.create_from_run`` —
    NOT re-implement persistence. Cycle 10 INTEGRATE A2 contract.

    Patches ``create_from_run`` to capture the dispatch arguments. The
    GREEN step's handler body must:

      * construct a ``ValidationSuiteService`` (or use an injected one),
      * pass ``run_id`` (positional or keyword),
      * pass ``label`` keyword,
      * pass ``tolerance_abs`` keyword.
    """
    from app.schemas.validation_suite import (
        BaselineScoresPayload,
        ValidationSuiteOut,
    )
    from app.tools.save_suite import handle_save_suite

    # Build a real-shape ValidationSuiteOut so the handler's downstream
    # serialization to SaveSuiteOutput sees the expected fields. The mock
    # returns this without writing to the DB.
    canonical_out = ValidationSuiteOut(
        id=uuid.uuid4().hex,
        source_run_id=uuid.uuid4().hex,
        label="cycle-10-persists-test",
        tolerance_abs=0.5,
        project_id=None,
        repo_full_name="acme/widget",
        created_at=datetime.now(UTC),
        retired_at=None,
        retired_reason=None,
        prompts_snapshot=[
            {
                "raw_prompt": f"raw prompt {i}",
                "intent_label": None,
                "original_optimization_id": None,
            }
            for i in range(3)
        ],
        baseline_scores=BaselineScoresPayload(
            mean_overall=7.85,
            p5_overall=6.2,
            p50_overall=7.8,
            p95_overall=9.1,
            per_prompt=[],
            task_type_distribution={},
        ),
    )

    run = await _seed_run(
        db, aggregate=_canonical_aggregate(),
        prompt_results=_prompt_results(3),
    )

    with patch(
        "app.services.validation_suite_service."
        "ValidationSuiteService.create_from_run",
        new_callable=AsyncMock,
        return_value=canonical_out,
    ) as patched_create:
        await handle_save_suite(
            run_id=run.id,
            label="cycle-10-persists-test",
            tolerance_abs=0.5,
            write_queue=write_queue,
        )

    assert patched_create.call_count == 1, (
        f"Expected exactly 1 call to ValidationSuiteService.create_from_run "
        f"— got {patched_create.call_count}. The handler must delegate to "
        f"the service (Cycle 10 INTEGRATE A2)."
    )

    # Inspect the call — must pass run_id (pos or kw), label, tolerance_abs.
    args, kwargs = patched_create.call_args
    call_run_id = kwargs.get("run_id") or (args[0] if args else None)
    assert call_run_id == run.id, (
        f"create_from_run must receive run_id={run.id!r}; got {call_run_id!r}"
    )
    assert kwargs.get("label") == "cycle-10-persists-test", (
        f"create_from_run must receive label='cycle-10-persists-test' "
        f"(keyword), got {kwargs.get('label')!r}"
    )
    assert kwargs.get("tolerance_abs") == 0.5, (
        f"create_from_run must receive tolerance_abs=0.5 (keyword), "
        f"got {kwargs.get('tolerance_abs')!r}"
    )


# ===========================================================================
# Test 5 — synthesis_replay_suite returns ReplayInitiatedOutput
# ===========================================================================


async def test_synthesis_replay_suite_returns_replay_initiated_output_with_202_semantics(
    db: AsyncSession, stub_orchestrator,
):
    """The handler must return ``ReplayInitiatedOutput`` with the spec §4
    line 327 field set: ``run_id``, ``suite_id``, ``mode='replay_run'``,
    ``poll_url``, ``started_at``.

    202 semantics — the response is constructed BEFORE the replay generator
    completes per spec §4 line 426-442. The MCP handler dispatches through
    ``RunOrchestrator.dispatch_async()`` (not ``run()``) so the response
    surfaces immediately with the placeholder ``run_id`` minted by the
    orchestrator's initial INSERT path.
    """
    from app.schemas.mcp_models import ReplayInitiatedOutput
    from app.tools.replay_suite import handle_replay_suite

    suite = await _seed_active_suite(db)

    result = await handle_replay_suite(suite_id=suite.id)

    assert isinstance(result, ReplayInitiatedOutput), (
        f"synthesis_replay_suite must return ReplayInitiatedOutput "
        f"(spec §4 line 327). Got {type(result).__name__!r}."
    )
    # Body shape pinned per spec §4 line 426-442.
    assert isinstance(result.run_id, str) and result.run_id, (
        "run_id must be a non-empty string (RunOrchestrator-minted UUID)."
    )
    assert result.suite_id == suite.id, (
        f"suite_id must echo the dispatched suite_id={suite.id!r}; "
        f"got {result.suite_id!r}."
    )
    assert result.mode == "replay_run", (
        f"mode must be the Literal 'replay_run'; got {result.mode!r}."
    )
    assert isinstance(result.poll_url, str) and result.poll_url, (
        "poll_url must be a non-empty string (typically /api/runs/{run_id})."
    )
    assert isinstance(result.started_at, datetime), (
        f"started_at must be a datetime instance; "
        f"got {type(result.started_at).__name__!r}."
    )


# ===========================================================================
# Test 6 — synthesis_replay_suite returns suite_retired envelope on retired
# ===========================================================================


async def test_synthesis_replay_suite_returns_suite_retired_error_envelope_on_retired(
    db: AsyncSession, stub_orchestrator,
):
    """Replay against a RETIRED suite (``retired_at IS NOT NULL``) must
    surface the structured error envelope code ``suite_retired`` per
    spec §4 line 344.

    The handler must check the suite's ``retired_at`` BEFORE dispatching
    through the orchestrator — a retired suite cannot kick off a new
    replay run (no orphan ``RunRow`` written).
    """
    from app.tools.replay_suite import handle_replay_suite

    suite = await _seed_retired_suite(db)

    with pytest.raises(Exception, match=r"suite_retired") as exc_info:
        await handle_replay_suite(suite_id=suite.id)

    exc = exc_info.value
    error_text = str(exc)
    code_attr = (
        getattr(exc, "code", None)
        or getattr(exc, "reason", None)
        or getattr(exc, "detail", None)
    )
    assert (
        "suite_retired" in error_text
        or code_attr == "suite_retired"
    ), (
        f"Expected error envelope with code='suite_retired' "
        f"(spec §4 line 344). Got exception text={error_text!r}, "
        f"code/reason/detail={code_attr!r}."
    )


# ===========================================================================
# Test 7 — synthesis_replay_suite uses replay_run_persist op_label
# ===========================================================================


async def test_synthesis_replay_suite_uses_replay_run_persist_op_label(
    db: AsyncSession, stub_orchestrator,
):
    """The dispatch path must surface ``operation_label='replay_run_persist'``
    on at least one ``WriteQueue.submit()`` call — per spec §4 line 474 +
    spec §10 Cycle 6 GREEN step 4. This is mode-keyed via
    ``RunOrchestrator._persist_final`` so the JSONL trace can filter on
    replay terminal persists.

    The test patches ``WriteQueue.submit`` to capture every
    ``operation_label`` value and asserts the canonical label appears in
    the captured set.

    A naive GREEN implementation that bypasses the orchestrator (writing
    the placeholder RunRow directly) would miss this label — the test
    fails fast and flags the architectural drift.
    """
    from app.tools.replay_suite import handle_replay_suite

    orchestrator, _replay_gen = stub_orchestrator
    suite = await _seed_active_suite(db)

    captured_labels: list[str | None] = []
    original_submit = orchestrator._write_queue.submit

    async def _spy_submit(work, *, timeout=None, operation_label=None):
        captured_labels.append(operation_label)
        return await original_submit(
            work, timeout=timeout, operation_label=operation_label,
        )

    with patch.object(
        orchestrator._write_queue, "submit", side_effect=_spy_submit,
    ):
        await handle_replay_suite(suite_id=suite.id)

    assert "replay_run_persist" in captured_labels, (
        f"Expected at least one WriteQueue.submit call with "
        f"operation_label='replay_run_persist' (spec §4 line 474, "
        f"mode-keyed via RunOrchestrator._persist_final). Captured "
        f"labels: {captured_labels!r}."
    )


# ===========================================================================
# Test 8 — synthesis_replay_suite dispatches through RunOrchestrator
# ===========================================================================


async def test_synthesis_replay_suite_dispatches_through_run_orchestrator(
    db: AsyncSession, stub_orchestrator,
):
    """The handler must call ``RunOrchestrator.dispatch_async`` (or
    ``RunOrchestrator.run``) with ``mode='replay_run'`` and
    ``request.payload['suite_id']`` set.

    Spec §10 Cycle 10 RED line 725: patch
    ``app.state.run_orchestrator.dispatch_async``; assert called with
    mode='replay_run'. The MCP-process equivalent is ``get_run_orchestrator``
    (Cycle 13 DI helper) — this test patches the dispatch method on the
    real orchestrator installed by the ``stub_orchestrator`` fixture, which
    covers both surfaces.
    """
    from app.services.run_orchestrator import RunOrchestrator
    from app.tools.replay_suite import handle_replay_suite

    orchestrator, _replay_gen = stub_orchestrator
    suite = await _seed_active_suite(db)

    # Capture all dispatch-style invocations. ``run()`` calls
    # ``dispatch_async`` internally so we instrument both to satisfy whichever
    # method the GREEN step picks.
    captured: list[tuple[str, str, Any]] = []
    original_dispatch = RunOrchestrator.dispatch_async
    original_run = RunOrchestrator.run

    async def _spy_dispatch(self, *, mode, request, run_id, **kwargs):
        captured.append(("dispatch_async", mode, request))
        return await original_dispatch(
            self, mode=mode, request=request, run_id=run_id, **kwargs,
        )

    async def _spy_run(self, mode, request, *, run_id=None):
        captured.append(("run", mode, request))
        return await original_run(self, mode, request, run_id=run_id)

    with patch.object(RunOrchestrator, "dispatch_async", _spy_dispatch), \
         patch.object(RunOrchestrator, "run", _spy_run):
        await handle_replay_suite(suite_id=suite.id)

    assert captured, (
        "Neither RunOrchestrator.dispatch_async nor "
        "RunOrchestrator.run was invoked — the handler must dispatch "
        "through the orchestrator (Cycle 10 INTEGRATE A2 + spec §10 "
        "Cycle 10 RED test 8)."
    )

    # At least one captured call must have mode='replay_run'.
    modes = [c[1] for c in captured]
    assert "replay_run" in modes, (
        f"Expected at least one dispatch with mode='replay_run'; got "
        f"modes={modes!r}."
    )

    # The captured RunRequest payload must carry ``suite_id`` so the
    # orchestrator's _create_row threads it onto the placeholder RunRow.
    payloads = [c[2].payload for c in captured if hasattr(c[2], "payload")]
    assert any(p.get("suite_id") == suite.id for p in payloads), (
        f"Expected at least one dispatch whose RunRequest.payload carries "
        f"suite_id={suite.id!r}; got payloads={payloads!r}."
    )


# ===========================================================================
# Test 9 — MCP tool count is now 18 (17 prior + synthesis_refresh_seed_agent)
# ===========================================================================


async def test_mcp_tool_count_is_now_17():
    """The FastMCP tool registry must hold EXACTLY 18 tools — the 17
    post-Cycle-10 tools plus ``synthesis_refresh_seed_agent`` (T3.5 v0.4.29).

    Spec §10 Cycle 10 RED test 9 + spec §4 line 322. Mirrors the
    ``test_15th_mcp_tool_registered`` canonical pattern in
    ``backend/tests/test_probe_mcp_tool.py:140``.

    A drift of +1 (only one tool registered) or +0 (neither registered)
    is the canonical RED signal — the GREEN step must register both
    decorators in ``app/mcp_server.py``.

    T3.5 (v0.4.29): added ``synthesis_refresh_seed_agent`` so the count
    is bumped from 17 to 18; the presence assertions on the original
    Cycle-10 tools remain load-bearing for spec §10 Cycle 10.
    """
    from app import mcp_server

    tools = list(mcp_server.mcp._tool_manager._tools.keys())  # type: ignore[attr-defined]

    assert "synthesis_save_suite" in tools, (
        f"synthesis_save_suite missing from MCP tool registry. "
        f"Registered tools: {sorted(tools)!r}"
    )
    assert "synthesis_replay_suite" in tools, (
        f"synthesis_replay_suite missing from MCP tool registry. "
        f"Registered tools: {sorted(tools)!r}"
    )
    assert "synthesis_refresh_seed_agent" in tools, (
        f"synthesis_refresh_seed_agent missing from MCP tool registry. "
        f"Registered tools: {sorted(tools)!r}"
    )
    assert len(tools) == 18, (
        f"Expected exactly 18 tools after T3.5 v0.4.29 "
        f"(17 post-Cycle-10 + synthesis_refresh_seed_agent). "
        f"Got {len(tools)}: {sorted(tools)!r}"
    )
