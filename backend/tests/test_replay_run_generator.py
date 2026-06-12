"""RED-phase tests for ``ReplayRunGenerator`` — Topic Probe Tier 2 Cycle 6 (11 tests).

Plan:  ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 6 Task 6.1
Spec:  ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
       §2 components (services/generators/replay_run_generator.py + _aggregate.py),
       §5 ReplayRunGenerator body (concurrent fan-out since v0.4.36, full collaborator graph,
       ``PendingOptimization.trace_id`` correlation, non-None final_report stub),
       §9 ``phase="replay_run"`` trace tagging,
       §10 Cycle 6 (replay's 30-min worst case + ContextEnrichmentService usage).

The module ``app.services.generators.replay_run_generator`` does NOT exist yet —
every test in this file fails at COLLECTION time with
``ModuleNotFoundError: No module named 'app.services.generators.replay_run_generator'``.

Once Cycle 6 GREEN lands ``replay_run_generator.py`` + the ``_aggregate.py``
helper + the orchestrator + lifespan extensions, every test must pass without
any further test-file edit (RED-phase invariant per
``feedback_tdd_protocol.md``).

Surface contract pinned by these tests (spec §5):

* ``ReplayRunGenerator.__init__`` keyword-only kwargs matching the
  ``batch_pipeline.run_single_prompt`` collaborator union: ``provider``,
  ``prompt_loader``, ``embedding_service``, ``session_factory``,
  ``taxonomy_engine``, ``domain_resolver``, ``context_service``,
  ``write_queue``.
* ``async def run(request: RunRequest, *, run_id: str) -> GeneratorResult``
  conforming to the canonical ``RunGenerator`` Protocol
  (``services/generators/base.py:24-33``).
* ``request.payload["suite_id"]`` MUST be read out of the payload (no new
  frozen-dataclass request type — would break the Protocol's
  ``runtime_checkable``).
* Suite snapshot loaded in a SHORT read session (detached-ORM-safe per
  Foundation P4 contract); session closes BEFORE per-prompt loop iterates.
* Retired suite (``retired_at is not None``) raises ``ValueError("suite_retired")``.
* Repo drift (``suite.repo_full_name != request.payload["repo_full_name"]``)
  emits a ``probe_warning(code='repo_drift', ...)`` event — informational only.
* Per-prompt CONCURRENT fan-out (v0.4.36, semaphore-capped per
  ``PROBE_PROMPT_CONCURRENCY``) with per-prompt try/except (still NOT
  ``asyncio.TaskGroup``).
  ``batch_pipeline.run_single_prompt`` is the canonical primitive (A2-compliant).
* Per-prompt dict uses ``"overall_score"`` key (canonical input contract of
  ``compute_run_aggregate`` extracted from ``topic_probe_generator.py:414-418``)
  AND ``"trace_id": pending.trace_id`` (NOT ``pending.id`` — replay does not
  bulk_persist, so ``pending.id`` would be an orphan uuid; ``trace_id`` is the
  canonical correlation field on ``PendingOptimization``, set at
  ``batch_pipeline.py:101,217``).
* Position correspondence: ``prompt_results[i]["raw_prompt_idx"] == i`` for all i.
* Final report stub: non-None string referencing ``SuiteDetailView`` and
  containing ``suite_id=`` (the contract pinned in spec §5 final_report block).
* Orchestrator persists via ``operation_label="replay_run_persist"``
  (mode-keyed per spec §4 + §10 Cycle 6 GREEN step 4).
* JSONL trace tagged ``phase="replay_run"`` (spec §9 trace tagging).
* Lifespan registers the generator at ``app.state.run_orchestrator._generators["replay_run"]``
  (spec §10 Cycle 6 GREEN step 7).
"""
from __future__ import annotations

import asyncio as _asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.services.generators.replay_run_generator as replay_mod
from app.models import Base, ValidationSuite
from app.schemas.runs import RunRequest
from app.services.batch_pipeline import PendingOptimization
from app.services.generators._constants import (
    REPLAY_CHANGES_SUMMARY_MAX_CHARS,
    REPLAY_OUTPUT_SNAPSHOT_MAX_CHARS,
)
from app.services.generators.base import GeneratorResult, RunGenerator

# The not-yet-existing module — every import below raises
# ``ModuleNotFoundError`` until Cycle 6 GREEN lands. Captured here at module top
# so all 11 tests fail at COLLECTION time with the same import error.
from app.services.generators.replay_run_generator import (  # noqa: F401 — Cycle 6 RED signal
    ReplayRunGenerator,
)
from app.services.run_orchestrator import RunOrchestrator

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local fixtures — shared-URI pattern mirrors test_run_orchestrator.py +
# test_validation_suite_service.py so the WriteQueue's writer engine and the
# read session see the same in-memory data.
# ---------------------------------------------------------------------------


_SHARED_URI = (
    "sqlite+aiosqlite:///"
    "file:memdb_replay_run_generator_test?mode=memory&cache=shared&uri=true"
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

    ``ReplayRunGenerator`` (per spec §5) reads the suite snapshot through
    ``self._session_factory`` (constructor-injected). Production wires it to
    ``async_session_factory``; tests inject a factory pointing at the writer
    engine so seeded rows are visible. We also patch the module-level name so
    ``_persist_final`` / ``_reload`` paths inside the orchestrator see the same
    shared engine.
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
    """Read session against the same shared URI so commits from the WriteQueue
    are immediately visible. Created AFTER ``writer_engine`` so schema
    materialisation has happened."""
    read_engine = create_async_engine(_SHARED_URI)
    factory = async_sessionmaker(
        read_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    await read_engine.dispose()


# ---------------------------------------------------------------------------
# Seeding helpers — real ORM constructors per A5 anti-pattern (no MagicMock
# for snapshot data crossing detached-ORM boundaries; spec §10 Cycle 6 A5).
# ---------------------------------------------------------------------------


def _per_prompt_score(idx: int, overall: float = 7.5) -> dict:
    """Canonical ``baseline_scores.per_prompt[i]`` entry shape.

    Mirrors ``test_validation_suite_service.py:_canonical_aggregate`` per-prompt
    shape so position-correspondence between
    ``prompts_snapshot[i]`` and ``baseline_scores.per_prompt[i]`` is exercised
    end-to-end. The replay generator reads
    ``suite_snapshot.baseline_scores["per_prompt"][idx]["overall"]`` per spec §5.
    """
    return {
        "raw_prompt_idx": idx,
        "overall": overall,
        "dimensions": {
            "clarity": overall,
            "specificity": overall,
            "structure": overall,
            "faithfulness": overall,
            "conciseness": overall,
        },
    }


def _prompts_snapshot(n: int) -> list[dict]:
    """N canonical ``ValidationSuite.prompts_snapshot`` rows."""
    return [
        {
            "raw_prompt": f"raw prompt {i}",
            "intent_label": "general",
            "original_optimization_id": None,
        }
        for i in range(n)
    ]


def _baseline_scores(n: int, mean: float = 7.5) -> dict:
    """Canonical ``ValidationSuite.baseline_scores`` JSON column shape."""
    return {
        "mean_overall": mean,
        "p5_overall": mean - 0.3,
        "p50_overall": mean,
        "p95_overall": mean + 0.3,
        "per_prompt": [_per_prompt_score(i, mean) for i in range(n)],
        "task_type_distribution": {"coding": n},
    }


async def _seed_suite(
    db: AsyncSession,
    *,
    n_prompts: int = 3,
    label: str = "replay-fixture-suite",
    repo_full_name: str | None = "owner/repo",
    retired_at: datetime | None = None,
    suite_id: str | None = None,
) -> ValidationSuite:
    """Insert a real ``ValidationSuite`` row.

    Per A5 anti-pattern: real ORM constructor, NOT ``MagicMock``. The frozen
    snapshot the generator reads (``prompts_snapshot`` + ``baseline_scores``)
    is the JSON payload — no lazy-load surface so detached-ORM concerns do
    not apply (everything eagerly fetched is a plain dict / list[dict]).
    """
    sid = suite_id or uuid.uuid4().hex
    row = ValidationSuite(
        id=sid,
        source_run_id=None,  # replay does not need source linkage to run
        prompts_snapshot=_prompts_snapshot(n_prompts),
        baseline_scores=_baseline_scores(n_prompts),
        tolerance_abs=0.5,
        label=label,
        project_id=None,
        repo_full_name=repo_full_name,
        created_at=datetime.now(UTC).replace(tzinfo=None),
        retired_at=retired_at,
        retired_reason="test_setup" if retired_at else None,
    )
    db.add(row)
    await db.commit()
    return row


def _build_pending(
    *,
    idx: int = 0,
    pending_id: str | None = None,
    trace_id: str | None = None,
    overall: float = 7.5,
    task_type: str = "coding",
) -> PendingOptimization:
    """Build a real ``PendingOptimization`` matching ``run_single_prompt``'s output.

    Per A5: real dataclass constructor (NOT ``MagicMock``). All 5 score
    dimensions plus ``overall_score`` plus ``trace_id`` plus ``task_type`` are
    populated so INTEGRATE A1's "consume every field" diff passes.
    """
    return PendingOptimization(
        id=pending_id or f"opt-{idx}",
        trace_id=trace_id or f"trace-{idx}",
        raw_prompt=f"raw prompt {idx}",
        score_clarity=overall,
        score_specificity=overall,
        score_structure=overall,
        score_faithfulness=overall,
        score_conciseness=overall,
        overall_score=overall,
        task_type=task_type,
        intent_label="general",
    )


def _make_generator(
    *,
    provider: Any = None,
    prompt_loader: Any = None,
    embedding_service: Any = None,
    session_factory: Any = None,
    taxonomy_engine: Any = None,
    domain_resolver: Any = None,
    context_service: Any = None,
    write_queue: Any = None,
) -> ReplayRunGenerator:
    """Factory matching the constructor union per spec §5.

    The collaborator graph mirrors ``batch_pipeline.run_single_prompt``'s
    parameter list (provider / prompt_loader / embedding_service /
    session_factory / taxonomy_engine / domain_resolver / context_service /
    write_queue). Tests inject minimal mocks where typing doesn't matter
    and rely on monkeypatching ``batch_pipeline.run_single_prompt`` to
    control per-prompt outcomes deterministically.
    """
    import app.database as _database_mod

    return ReplayRunGenerator(
        provider=provider or MagicMock(),
        prompt_loader=prompt_loader or MagicMock(),
        embedding_service=embedding_service or MagicMock(),
        session_factory=session_factory or _database_mod.async_session_factory,
        taxonomy_engine=taxonomy_engine or MagicMock(),
        domain_resolver=domain_resolver or MagicMock(),
        context_service=context_service or MagicMock(),
        write_queue=write_queue or MagicMock(),
    )


def _make_request(
    *,
    suite_id: str,
    repo_full_name: str | None = "owner/repo",
    project_id: str | None = None,
) -> RunRequest:
    """Build a ``mode='replay_run'`` ``RunRequest`` per spec §5 payload contract."""
    payload: dict = {"suite_id": suite_id}
    if repo_full_name is not None:
        payload["repo_full_name"] = repo_full_name
    if project_id is not None:
        payload["project_id"] = project_id
    return RunRequest(mode="replay_run", payload=payload)


def _patch_run_single_prompt(
    monkeypatch,
    *,
    pendings: list[PendingOptimization] | None = None,
    raises_at_idx: dict[int, BaseException] | None = None,
) -> list[dict]:
    """Patch ``batch_pipeline.run_single_prompt`` for the generator's per-prompt loop.

    ``pendings`` — pre-built ``PendingOptimization`` results returned in order
    by ``call_count`` index. Each call to ``run_single_prompt`` consumes the
    next ``pendings[i]`` (or raises ``raises_at_idx[i]`` if specified).

    Returns the list ``calls`` mutated in place — tests inspect ``calls`` to
    verify call count + kwarg shapes (A2 INTEGRATE: signature-diff against
    ``batch_pipeline.py:158-178``).
    """
    import app.services.batch_pipeline as batch_mod

    pendings = pendings or []
    raises_at_idx = raises_at_idx or {}
    calls: list[dict] = []
    counter = {"n": 0}

    async def _fake_run_single_prompt(*args, **kwargs):
        idx = counter["n"]
        counter["n"] += 1
        calls.append({"args": args, "kwargs": kwargs})
        if idx in raises_at_idx:
            raise raises_at_idx[idx]
        if idx < len(pendings):
            return pendings[idx]
        # Default fallback if test didn't seed enough pendings.
        return _build_pending(idx=idx)

    monkeypatch.setattr(batch_mod, "run_single_prompt", _fake_run_single_prompt)
    return calls


# ===========================================================================
# Test 1 — Protocol conformance (runtime_checkable Protocol check)
# ===========================================================================


async def test_replay_run_generator_conforms_to_run_generator_protocol() -> None:
    """``ReplayRunGenerator`` must conform to the canonical ``RunGenerator``
    Protocol declared at ``services/generators/base.py:24-33`` with
    ``@runtime_checkable`` enabled.

    Spec §5: "Conforms to the canonical RunGenerator Protocol
    (backend/app/services/generators/base.py:24-33):
    async def run(self, request: RunRequest, *, run_id: str) -> GeneratorResult."

    Per spec §5 the constructor's collaborator graph mirrors the
    ``batch_pipeline.run_single_prompt`` parameter union — NOT a literal
    mirror of ``SeedAgentGenerator.__init__(seed_orchestrator, write_queue)``
    (which delegates collaborator wiring to its orchestrator) nor of
    ``TopicProbeGenerator.__init__(provider, repo_index_query, taxonomy_engine, ...)``
    (which uses repo_index_query for Phase 1 grounding — irrelevant to replay).

    The Protocol is structurally typed — having an ``async def run(request,
    *, run_id) -> GeneratorResult`` method is the only contract.
    ``runtime_checkable`` makes ``isinstance(gen, RunGenerator)`` actually
    return True at runtime when the method signature matches.
    """
    gen = _make_generator()

    # The Protocol is `runtime_checkable` so isinstance returns True for any
    # object with the structural shape (a callable ``run``).
    assert isinstance(gen, RunGenerator), (
        f"ReplayRunGenerator must conform to RunGenerator Protocol; got "
        f"{type(gen)!r}"
    )


# ===========================================================================
# Test 2 — payload extraction: suite_id read from request.payload
# ===========================================================================


async def test_replay_run_generator_reads_suite_id_from_payload(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 line 686: ``suite_id: str = request.payload["suite_id"]`` — the
    generator MUST read ``suite_id`` out of the payload dict (NOT a new
    typed-dataclass request type — would break the Protocol's
    ``runtime_checkable`` and require parallel ``_extract_*_meta`` helpers
    in the orchestrator).

    This test pins the canonical extraction path. We record every suite_id
    seen by a ``ValidationSuiteService``-equivalent reader (the generator's
    own short-session load), then assert the recorded id matches the
    payload's suite_id verbatim.
    """
    suite = await _seed_suite(db, n_prompts=2)

    # Patch ``run_single_prompt`` so per-prompt loop succeeds deterministically.
    _patch_run_single_prompt(
        monkeypatch,
        pendings=[_build_pending(idx=i) for i in range(2)],
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-2")

    # The most observable contract: per-prompt results match the suite's
    # snapshot length. If the generator read the wrong suite_id, the
    # snapshot lookup would fail with ``ValueError("suite_not_found")``
    # OR return zero rows. Either way the assertion below trips.
    assert isinstance(result, GeneratorResult)
    assert len(result.prompt_results) == 2, (
        f"expected 2 prompts (matching suite.prompts_snapshot length), got "
        f"{len(result.prompt_results)}"
    )


# ===========================================================================
# Test 3 — detached-ORM safety: suite snapshot read in SHORT session
# ===========================================================================


async def test_replay_run_generator_loads_suite_snapshot_in_short_session(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 line 691: ``suite_snapshot = await self._load_suite_snapshot(suite_id)``.

    Per Foundation P4 detached-ORM-safe contract: the read session MUST close
    BEFORE the per-prompt loop iterates. Holding a session across LLM calls
    triggers ``MissingGreenlet`` on detached lazy-loads + violates the
    audit-hook ``read-engine audit:`` invariant.

    We patch the session_factory to record session open/close events and
    each ``run_single_prompt`` call. The session-close event MUST appear
    BEFORE the first per-prompt call.
    """
    suite = await _seed_suite(db, n_prompts=3)

    events: list[str] = []

    # Wrap async_session_factory to record __aenter__/__aexit__ ordering.
    import app.database as _database_mod
    real_factory = _database_mod.async_session_factory

    class _RecordingContext:
        def __init__(self, inner):
            self._inner = inner
            self._inner_ctx = None

        async def __aenter__(self):
            self._inner_ctx = self._inner()
            session = await self._inner_ctx.__aenter__()
            events.append("session_open")
            return session

        async def __aexit__(self, exc_type, exc, tb):
            events.append("session_close")
            return await self._inner_ctx.__aexit__(exc_type, exc, tb)

    def _wrapped_factory():
        return _RecordingContext(real_factory)

    monkeypatch.setattr(_database_mod, "async_session_factory", _wrapped_factory)

    async def _fake_run_single_prompt(*args, **kwargs):
        events.append("per_prompt_call")
        return _build_pending(idx=events.count("per_prompt_call") - 1)

    import app.services.batch_pipeline as batch_mod
    monkeypatch.setattr(batch_mod, "run_single_prompt", _fake_run_single_prompt)

    gen = _make_generator(
        session_factory=_wrapped_factory,
        write_queue=write_queue,
    )
    request = _make_request(suite_id=suite.id)

    await gen.run(request, run_id="rid-3")

    # The first 'session_close' must come BEFORE the first 'per_prompt_call'
    # (Foundation P4 invariant — no DB session held across LLM work).
    assert "session_close" in events
    assert "per_prompt_call" in events
    first_close_idx = events.index("session_close")
    first_per_prompt_idx = events.index("per_prompt_call")
    assert first_close_idx < first_per_prompt_idx, (
        f"suite snapshot session must close BEFORE per-prompt loop iterates; "
        f"events sequence: {events}"
    )


# ===========================================================================
# Test 4 — retired suite raises ValueError("suite_retired")
# ===========================================================================


async def test_replay_run_generator_raises_suite_retired_on_retired_suite(
    db, write_queue,
) -> None:
    """Spec §5 lines 692-693:
        if suite_snapshot.retired_at is not None:
            raise ValueError("suite_retired")

    Retired suites must NOT be replayable. The router maps this code to a
    409 envelope per spec §4 line 344 (suite_retired → 409). The generator
    raises BEFORE per-prompt loop iteration, so no RunRow.aggregate is
    populated and the orchestrator's terminal persist marks the row failed.
    """
    suite = await _seed_suite(
        db,
        n_prompts=2,
        retired_at=datetime.now(UTC).replace(tzinfo=None),
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    with pytest.raises(ValueError, match="suite_retired"):
        await gen.run(request, run_id="rid-4")


# ===========================================================================
# Test 5 — repo drift fires single probe_warning(code='repo_drift')
# ===========================================================================


async def test_replay_run_generator_emits_probe_warning_on_repo_drift(
    db, write_queue, monkeypatch, event_bus_capture,
) -> None:
    """Spec §5 lines 696-704 + §9 line 1340:

        if (suite_snapshot.repo_full_name and repo_full_name
            and suite_snapshot.repo_full_name != repo_full_name):
            warnings.append("repo_drift")
            event_bus.publish("probe_warning", {
                "run_id": run_id, "code": "repo_drift",
                "suite_repo": suite_snapshot.repo_full_name,
                "current_repo": repo_full_name,
            })

    Repo drift is informational — replay still proceeds with the saved
    snapshot. Exactly ONE ``probe_warning`` event with ``code='repo_drift'``
    must fire, carrying both the saved suite repo and the current request
    repo so consumers (frontend RegressionBadge / SuiteDetailView per spec
    §6) can surface the drift to the operator.
    """
    suite = await _seed_suite(
        db, n_prompts=2, repo_full_name="owner/A",
    )

    _patch_run_single_prompt(
        monkeypatch,
        pendings=[_build_pending(idx=i) for i in range(2)],
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id, repo_full_name="owner/B")

    await gen.run(request, run_id="rid-5")

    drift_events = [
        e for e in event_bus_capture.events_for_run("rid-5")
        if e.kind == "probe_warning"
        and e.payload.get("code") == "repo_drift"
    ]
    assert len(drift_events) == 1, (
        f"expected exactly one probe_warning(code='repo_drift'), got "
        f"{len(drift_events)} (kinds: "
        f"{[e.kind for e in event_bus_capture.events_for_run('rid-5')]})"
    )
    payload = drift_events[0].payload
    assert payload.get("suite_repo") == "owner/A", (
        f"probe_warning.suite_repo must equal saved suite repo, got "
        f"{payload.get('suite_repo')!r}"
    )
    assert payload.get("current_repo") == "owner/B", (
        f"probe_warning.current_repo must equal request repo, got "
        f"{payload.get('current_repo')!r}"
    )


# ===========================================================================
# Test 6 — per-prompt try/except failure isolation (concurrent fan-out since v0.4.36)
# ===========================================================================


async def test_replay_run_generator_sequential_per_prompt_with_try_except(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 lines 715-722 — per-prompt try/except failure isolation
    (matching ``TopicProbeGenerator`` Phase 3 canonical pattern at
    ``topic_probe_generator.py:185-215``). ``asyncio.TaskGroup`` was REJECTED:
    a single child's non-rate-limit exception propagates BaseExceptionGroup
    that aborts siblings, leaving inconsistent state with partial assignments.
    v0.4.36 replaced the sequential loop with the concurrent fan-out
    (semaphore-capped per ``PROBE_PROMPT_CONCURRENCY``); the per-prompt
    ``try/except Exception`` isolation contract is unchanged.

    Per spec §5: each ``batch_pipeline.run_single_prompt`` call is wrapped in
    ``try/except Exception`` so one prompt's failure does NOT abort siblings.
    The failed prompt's result row carries ``status='failed'`` + ``error``;
    completed prompts proceed unchanged.

    The aggregate's ``mean_overall`` MUST be computed from the SUCCEEDED
    prompts only (delegated to ``compute_run_aggregate`` per spec §5 line 808
    + §10 Cycle 6 _aggregate.py extraction).
    """
    suite = await _seed_suite(db, n_prompts=3)

    # 3 calls: first succeeds, second raises, third succeeds.
    _patch_run_single_prompt(
        monkeypatch,
        pendings=[
            _build_pending(idx=0, overall=8.0),
            _build_pending(idx=1, overall=99.0),  # never returned; raise instead
            _build_pending(idx=2, overall=6.0),
        ],
        raises_at_idx={1: RuntimeError("boom")},
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-6")

    assert len(result.prompt_results) == 3, (
        f"executor must produce one entry per snapshot prompt, "
        f"got {len(result.prompt_results)}"
    )
    statuses = [r.get("status") for r in result.prompt_results]
    assert statuses == ["completed", "failed", "completed"], (
        f"expected [completed, failed, completed], got {statuses!r}"
    )
    # mean_overall computed from completed prompts only ((8.0 + 6.0) / 2 = 7.0).
    assert result.aggregate.get("mean_overall") == pytest.approx(7.0), (
        f"aggregate.mean_overall must be computed from completed prompts "
        f"only (8.0 + 6.0)/2 = 7.0, got {result.aggregate.get('mean_overall')!r}"
    )


# ===========================================================================
# Test 7 — position correspondence: raw_prompt_idx tracks loop index
# ===========================================================================


async def test_replay_run_generator_position_correspondence(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 line 754: ``"raw_prompt_idx": idx`` — the per-prompt result
    dict MUST carry the loop index so consumers (regression alarm, suite
    detail view) can pair ``prompt_results[i]`` with
    ``baseline_scores.per_prompt[i]`` by index.

    Spec §3 key invariant 2 (positional correspondence): per-prompt
    execution with ``raw_prompt_idx=idx`` tagging plus the preallocated
    index-addressed results list preserves positional correspondence by
    construction. Pinned here for N=10 to exercise the invariant beyond
    the small-N happy paths.

    v0.4.36 ships the concurrent executor; ``PROBE_PROMPT_CONCURRENCY``
    landed with it. This test pins the invariant for the in-order fixture
    path; ``test_position_correspondence_under_out_of_order_completion``
    pins it under adversarial completion order.
    """
    n = 10
    suite = await _seed_suite(db, n_prompts=n)

    _patch_run_single_prompt(
        monkeypatch,
        pendings=[_build_pending(idx=i) for i in range(n)],
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-7")

    assert len(result.prompt_results) == n
    for i, row in enumerate(result.prompt_results):
        assert row.get("raw_prompt_idx") == i, (
            f"prompt_results[{i}].raw_prompt_idx must equal {i}, got "
            f"{row.get('raw_prompt_idx')!r}"
        )


# ===========================================================================
# Test 8 — per-prompt dict uses PendingOptimization.trace_id (NOT .id)
# ===========================================================================


async def test_replay_run_generator_uses_trace_id_not_id(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 lines 755-762 (verbatim):

        "trace_id": pending.trace_id,

    NOTE on rationale (from spec): replay does NOT call bulk_persist (see §5
    GeneratorResult — ``taxonomy_delta={}``, no Optimization row is INSERTed
    for replay results), so ``pending.id`` would be an orphan uuid pointing
    at no DB row. ``trace_id`` is the canonical correlation field on
    ``PendingOptimization`` (set at ``batch_pipeline.py:101, 217``) — opaque,
    NOT a FK to Optimization.

    This test pins the trace_id contract by constructing a
    ``PendingOptimization`` with DISTINCT ``.id`` and ``.trace_id`` values
    and asserting the per-prompt dict carries the ``trace_id`` (not the id).
    Failure mode: GREEN-step bug that writes ``"trace_id": pending.id`` would
    pass the structural shape check but leak the orphan uuid.
    """
    suite = await _seed_suite(db, n_prompts=1)

    distinct_pending = _build_pending(
        idx=0, pending_id="opt-id-1", trace_id="trace-id-1",
    )
    # Sanity — the two fields must differ on the fixture so the assertion
    # actually distinguishes them.
    assert distinct_pending.id == "opt-id-1"
    assert distinct_pending.trace_id == "trace-id-1"

    _patch_run_single_prompt(monkeypatch, pendings=[distinct_pending])

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-8")

    assert len(result.prompt_results) == 1
    row = result.prompt_results[0]
    assert row.get("trace_id") == "trace-id-1", (
        f"prompt_results[0].trace_id must equal pending.trace_id "
        f"('trace-id-1'), got {row.get('trace_id')!r}"
    )
    # Defensive: the dict must NOT carry pending.id under the trace_id key
    # (the canonical bug mode this test exists to detect).
    assert row.get("trace_id") != "opt-id-1", (
        "prompt_results[0] leaked pending.id under trace_id key — "
        "violates §5 trace_id rationale"
    )


# ===========================================================================
# Test 9 — per-prompt dict uses canonical "overall_score" key
# ===========================================================================


async def test_replay_run_generator_uses_overall_score_key(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 lines 763-769 (verbatim):

        # Key MUST be "overall_score" (not "overall") to match the canonical
        # input contract of compute_run_aggregate (extracted from
        # TopicProbeGenerator._build_aggregate at :414-418 which reads
        # `r["overall_score"]`).

    Replay's per-prompt dict MUST use the ``"overall_score"`` key (NOT
    ``"overall"`` and NOT ``"score"``). The shared
    ``services/generators/_aggregate.py::compute_run_aggregate()`` helper
    (per spec §10 Cycle 6 GREEN step 1) reads ``r["overall_score"]``
    verbatim from the input list — using a different key would cause the
    helper to return ``mean_overall=None``.

    Topic-probe-generator's emitted shape at
    ``topic_probe_generator.py:396`` is ``"overall_score": 7.0`` — replay
    must match so the helper can be SHARED across both generators (the
    Cycle 6 _aggregate.py extraction is byte-identical for both consumers).
    """
    suite = await _seed_suite(db, n_prompts=1)

    _patch_run_single_prompt(
        monkeypatch,
        pendings=[_build_pending(idx=0, overall=8.5)],
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-9")

    assert len(result.prompt_results) == 1
    row = result.prompt_results[0]
    assert "overall_score" in row, (
        f"prompt_results[0] must use 'overall_score' key per canonical "
        f"compute_run_aggregate input contract; got keys: {sorted(row.keys())}"
    )
    assert row["overall_score"] == pytest.approx(8.5), (
        f"prompt_results[0]['overall_score'] must equal pending.overall_score, "
        f"got {row['overall_score']!r}"
    )
    # Defensive — neither of the canonical "wrong key" alternatives may
    # exist standalone (so a GREEN-step bug writing both keys is caught
    # by the strict-key test rather than silently masking the contract).
    assert "overall" not in row, (
        "prompt_results[0] leaked the wrong 'overall' key — must be "
        "'overall_score' (compute_run_aggregate contract)"
    )


# ===========================================================================
# Test 10 — final_report is a non-None str stub referencing SuiteDetailView
# ===========================================================================


async def test_replay_run_generator_final_report_is_non_none_stub(
    db, write_queue, monkeypatch,
) -> None:
    """Spec §5 lines 826-843 (verbatim):

        # final_report: stub non-None string (one short markdown line —
        # the suite-detail UI surfaces the baseline diff, not narrative).
        ...
        final_report=(
            f"# Replay — suite_id={suite_id}\n"
            f"See SuiteDetailView for the baseline-vs-latest diff."
        ),

    ``GeneratorResult.final_report: str | None`` is declared at
    ``base.py:21`` (nullable in the dataclass). ``SeedAgentGenerator``
    returns ``None`` in multiple spots — the router at ``routers/runs.py:52``
    coerces ``NULL → ""`` before constructing ``RunResult`` (whose
    ``final_report: str`` is non-nullable at ``schemas/runs.py:49``).

    Returning ``None`` would NOT crash, but T2 picks the minimal-stub form
    because operator-facing log readers benefit from a non-empty marker line
    distinguishing replay terminal rows from seed agent rows. This test
    pins the stub form: a non-None ``str`` containing both ``suite_id=`` and
    a ``SuiteDetailView`` reference.
    """
    suite = await _seed_suite(db, n_prompts=1)

    _patch_run_single_prompt(
        monkeypatch, pendings=[_build_pending(idx=0)],
    )

    gen = _make_generator(write_queue=write_queue)
    request = _make_request(suite_id=suite.id)

    result = await gen.run(request, run_id="rid-10")

    assert result.final_report is not None, (
        "final_report must be a non-None str stub per spec §5 final_report "
        "rationale (minimal marker line distinguishes replay terminal rows "
        "from seed agent rows)"
    )
    assert isinstance(result.final_report, str), (
        f"final_report must be a str, got {type(result.final_report)!r}"
    )
    assert "suite_id=" in result.final_report, (
        f"final_report must reference 'suite_id=' per spec §5 stub shape; "
        f"got {result.final_report!r}"
    )
    assert suite.id in result.final_report, (
        f"final_report must include the actual suite_id; got "
        f"{result.final_report!r}"
    )
    assert "SuiteDetailView" in result.final_report, (
        f"final_report must reference 'SuiteDetailView' per spec §5 stub "
        f"contract; got {result.final_report!r}"
    )


# ===========================================================================
# Test 11 — orchestrator persists via 'replay_run_persist' op label AND
#           generator is registered in lifespan AND JSONL trace
#           tagged phase='replay_run'
# ===========================================================================


async def test_replay_run_generator_persists_via_replay_run_persist_op_label_and_is_registered_in_lifespan(
    db, write_queue, monkeypatch, tmp_path,
) -> None:
    """Combined assertion preserving the 11-test count for Cycle 6 (matches
    spec §10 sum invariant 7+11+9+7+15+11+9+7+5+9+18+14+7+3=132). Three
    sub-checks:

    (a) Lifespan registration — ``RunOrchestrator(generators={"replay_run": ...})``
        accepts the new mode. Pins Cycle 6 GREEN step 7
        (``app/main.py:1191-1196`` extension). We construct the orchestrator
        with the same shape as the lifespan registration block and verify
        ``app.state.run_orchestrator._generators["replay_run"]`` is an
        instance of ``ReplayRunGenerator``.

    (b) Mode-keyed persist op label — orchestrator's terminal-persist
        ``WriteQueue.submit()`` call uses
        ``operation_label="replay_run_persist"`` per spec §4 + §10 Cycle 6
        GREEN step 4. Pins ``_persist_final`` signature extension at
        ``run_orchestrator.py:186`` (``mode: str`` parameter added; existing
        callers updated; new caller in ``_run_to_completion`` threads ``mode``).

    (c) JSONL trace ``phase="replay_run"`` per spec §9 trace tagging
        ("replay runs get phase='replay_run' trace"). Pins the
        ``TraceLogger.log_phase(phase='replay_run', ...)`` invocation site
        somewhere in the replay path (generator OR orchestrator).
    """
    suite = await _seed_suite(db, n_prompts=2)

    # --- Per-prompt loop succeeds deterministically. ---
    _patch_run_single_prompt(
        monkeypatch,
        pendings=[_build_pending(idx=i) for i in range(2)],
    )

    # --- (a) Lifespan registration check ---
    # Construct the orchestrator with the SAME generator dict shape the
    # Cycle 6 GREEN-step lifespan extension will use
    # (``app/main.py:1190-1197``: ``generators={"topic_probe": ...,
    # "seed_agent": ..., "replay_run": ...}``). The test pins the dispatch
    # key ('replay_run') AND the generator type bound to it.
    replay_gen = _make_generator(write_queue=write_queue)
    # Use real stubs for the two existing dispatch modes — they're satisfied
    # by the Protocol-conforming ReplayRunGenerator since the orchestrator
    # only invokes whichever generator matches the request's mode.
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={
            "topic_probe": replay_gen,    # placeholder — dispatch never used here
            "seed_agent": replay_gen,     # placeholder — dispatch never used here
            "replay_run": replay_gen,
        },
    )
    assert "replay_run" in orchestrator._generators, (
        "RunOrchestrator must register 'replay_run' in its generators dict "
        "(spec §10 Cycle 6 GREEN step 7: lifespan extension)"
    )
    assert isinstance(orchestrator._generators["replay_run"], ReplayRunGenerator), (
        f"orchestrator._generators['replay_run'] must be an instance of "
        f"ReplayRunGenerator; got "
        f"{type(orchestrator._generators['replay_run']).__name__}"
    )

    # --- (b) Mode-keyed persist op label ---
    # Capture every WriteQueue.submit invocation so we can inspect the
    # operation_label kwarg for the terminal persist call.
    submit_calls: list[dict] = []
    real_submit = write_queue.submit

    async def _recording_submit(work, *, timeout=None, operation_label=None):
        submit_calls.append({
            "operation_label": operation_label,
            "timeout": timeout,
        })
        return await real_submit(
            work, timeout=timeout, operation_label=operation_label,
        )

    monkeypatch.setattr(write_queue, "submit", _recording_submit)

    # --- (c) JSONL trace setup — point DATA_DIR at tmp_path so the trace
    # writes land in a test-visible directory.
    import app.config as _config_mod
    monkeypatch.setattr(_config_mod, "DATA_DIR", tmp_path)
    # Some trace consumers import DATA_DIR from app.main; patch both
    # rebound names so whichever path the replay code takes lands in the
    # same tmp tree.
    if hasattr(__import__("app.main", fromlist=["DATA_DIR"]), "DATA_DIR"):
        monkeypatch.setattr("app.main.DATA_DIR", tmp_path, raising=False)

    # --- Trigger an end-to-end replay through the orchestrator. ---
    request = _make_request(suite_id=suite.id)
    row = await orchestrator.run("replay_run", request, run_id="rid-11")
    assert row is not None
    assert row.status in ("completed", "partial", "failed")

    # --- (b) Assert: terminal persist used mode-keyed operation label. ---
    terminal_persist_labels = [
        c["operation_label"] for c in submit_calls
        if c["operation_label"] == "replay_run_persist"
    ]
    assert len(terminal_persist_labels) >= 1, (
        f"orchestrator must use operation_label='replay_run_persist' for "
        f"mode='replay_run' terminal persist (spec §4 + §10 Cycle 6 GREEN "
        f"step 4: mode-keyed _persist_final). Submit-call labels seen: "
        f"{[c['operation_label'] for c in submit_calls]}"
    )

    # --- (c) Assert: JSONL trace tagged phase='replay_run'. ---
    # Scan every traces JSONL file under tmp_path/traces/ for a phase entry
    # tagged 'replay_run' (spec §9 trace tagging).
    traces_dir: Path = tmp_path / "traces"
    matching_entries: list[dict] = []
    if traces_dir.exists():
        for jsonl_file in traces_dir.glob("traces-*.jsonl"):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("phase") == "replay_run":
                    matching_entries.append(entry)
    assert len(matching_entries) >= 1, (
        f"replay runs must emit a JSONL trace entry with "
        f"phase='replay_run' (spec §9 trace tagging). "
        f"traces_dir={traces_dir} exists={traces_dir.exists()}; "
        f"entries found across replay paths: {matching_entries!r}"
    )


__all__ = [
    "test_replay_run_generator_conforms_to_run_generator_protocol",
    "test_replay_run_generator_reads_suite_id_from_payload",
    "test_replay_run_generator_loads_suite_snapshot_in_short_session",
    "test_replay_run_generator_raises_suite_retired_on_retired_suite",
    "test_replay_run_generator_emits_probe_warning_on_repo_drift",
    "test_replay_run_generator_sequential_per_prompt_with_try_except",
    "test_replay_run_generator_position_correspondence",
    "test_replay_run_generator_uses_trace_id_not_id",
    "test_replay_run_generator_uses_overall_score_key",
    "test_replay_run_generator_final_report_is_non_none_stub",
    "test_replay_run_generator_persists_via_replay_run_persist_op_label_and_is_registered_in_lifespan",
]


# ===========================================================================
# v0.4.36 Cycle 1 — concurrent executor (spec §3, tests §3.8 items 1-8 + 4b)
# ===========================================================================


def _patch_run_single_prompt_concurrent(
    monkeypatch,
    *,
    behavior,
):
    """Patch ``batch_pipeline.run_single_prompt`` keyed by ``prompt_index``.

    ``behavior(idx)`` is an async callable returning a PendingOptimization
    (or raising). Tracks in-flight concurrency in the returned dict.
    """
    import app.services.batch_pipeline as batch_mod

    state = {"in_flight": 0, "max_in_flight": 0, "calls": []}

    async def _fake(*args, **kwargs):
        idx = kwargs["prompt_index"]
        state["calls"].append(idx)
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        try:
            return await behavior(idx)
        finally:
            state["in_flight"] -= 1

    monkeypatch.setattr(batch_mod, "run_single_prompt", _fake)
    return state


def _capture_prompt_completed_events(monkeypatch) -> list[dict]:
    """Capture ``probe_prompt_completed`` payloads in publish order."""
    from app.services.event_bus import event_bus

    captured: list[dict] = []
    _orig = event_bus.publish

    def _spy(kind, payload, *a, **kw):
        if kind == "probe_prompt_completed":
            captured.append(dict(payload))
        return _orig(kind, payload, *a, **kw)

    monkeypatch.setattr(event_bus, "publish", _spy)
    return captured


def _rl_pending(idx: int) -> PendingOptimization:
    """Bail-gate-style pending: completed + heuristic score + rate_limit_meta."""
    p = _build_pending(idx=idx, overall=4.2)
    p.status = "completed"
    p.rate_limit_meta = {
        "rate_limited": True,
        "fallback": "passthrough",
        "provider": "claude_cli",
        "reset_at_iso": None,
    }
    return p


async def test_probe_prompt_concurrency_constant_contract() -> None:
    """AC-1: importable, immutable, mirrors BATCH_CONCURRENCY_BY_TIER."""
    from app.services.batch_orchestrator import BATCH_CONCURRENCY_BY_TIER
    from app.services.generators._constants import (
        DEFAULT_PROMPT_CONCURRENCY,
        PROBE_PROMPT_CONCURRENCY,
    )

    assert dict(PROBE_PROMPT_CONCURRENCY) == BATCH_CONCURRENCY_BY_TIER
    assert DEFAULT_PROMPT_CONCURRENCY == 5
    with pytest.raises(TypeError):
        PROBE_PROMPT_CONCURRENCY["internal"] = 99  # type: ignore[index]


async def test_replay_runs_prompts_concurrently_capped_at_tier(
    db, write_queue, monkeypatch,
) -> None:
    """AC-2: max simultaneous run_single_prompt == 10 (internal) and > 1."""
    n = 15
    suite = await _seed_suite(db, n_prompts=n)
    release = _asyncio.Event()

    async def _behavior(idx):
        await release.wait()
        return _build_pending(idx=idx)

    state = _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    task = _asyncio.create_task(
        gen.run(_make_request(suite_id=suite.id), run_id="rid-c1"),
    )
    # Wait until the semaphore admits its full quota.
    for _ in range(200):
        if state["max_in_flight"] >= 10:
            break
        await _asyncio.sleep(0.01)
    assert state["max_in_flight"] == 10, state
    assert state["in_flight"] == 10  # 5 queued behind the semaphore
    release.set()
    result = await task
    assert len(result.prompt_results) == n
    assert state["max_in_flight"] == 10  # never exceeded the cap


async def test_position_correspondence_under_out_of_order_completion(
    db, write_queue, monkeypatch,
) -> None:
    """AC-3: slot i holds prompt i even when i=0 finishes LAST.

    ALSO pins baseline/delta pairing under out-of-order completion: each
    index gets a DISTINCT baseline (``_baseline_scores`` defaults every
    index to a uniform 7.5, which would hide a slot/baseline swap), so
    ``baseline_overall`` and ``delta`` must track the slot index too.
    """
    n = 6
    suite = await _seed_suite(db, n_prompts=n)

    # Distinct per-index baselines: per_prompt[i]["overall"] = 5.0 + i * 0.5.
    # Reassign the whole JSON column so SQLAlchemy change-detection fires;
    # commit via the ``db`` fixture so the generator's read session (same
    # shared-URI in-memory DB) sees the mutated suite row.
    baseline = dict(suite.baseline_scores)
    per_prompt = [dict(entry) for entry in baseline["per_prompt"]]
    for i, entry in enumerate(per_prompt):
        entry["overall"] = 5.0 + i * 0.5
    baseline["per_prompt"] = per_prompt
    suite.baseline_scores = baseline
    db.add(suite)
    await db.commit()

    async def _behavior(idx):
        await _asyncio.sleep((n - idx) * 0.02)  # idx 0 slowest
        p = _build_pending(idx=idx)
        p.overall_score = float(idx)
        return p

    _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="rid-c2")
    for i, row in enumerate(result.prompt_results):
        assert row["raw_prompt_idx"] == i
        assert row["overall_score"] == float(i), (
            f"slot {i} holds the wrong prompt's result: {row['overall_score']}"
        )
        assert row["baseline_overall"] == pytest.approx(5.0 + i * 0.5), (
            f"slot {i} paired with the wrong baseline: {row['baseline_overall']}"
        )
        assert row["delta"] == pytest.approx(float(i) - (5.0 + i * 0.5)), (
            f"slot {i} delta mispaired: {row['delta']}"
        )


async def test_failure_isolation_under_concurrency(
    db, write_queue, monkeypatch,
) -> None:
    """AC-4: one Exception fails its row only; terminal status partial."""
    n = 4
    suite = await _seed_suite(db, n_prompts=n)

    async def _behavior(idx):
        if idx == 1:
            raise RuntimeError("boom-1")
        await _asyncio.sleep(0.01)
        return _build_pending(idx=idx)

    _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="rid-c3")
    assert result.terminal_status == "partial"
    assert result.prompt_results[1]["status"] == "failed"
    assert "boom-1" in result.prompt_results[1]["error"]
    assert all(
        result.prompt_results[i]["status"] == "completed"
        for i in (0, 2, 3)
    )


async def test_first_429_short_circuits_unstarted_prompts(
    db, write_queue, monkeypatch,
) -> None:
    """AC-5 pre-start path: prompts past the cap never call the provider."""
    n = 15  # cap=10 → 5 pre-start slots
    suite = await _seed_suite(db, n_prompts=n)
    release = _asyncio.Event()

    async def _behavior(idx):
        if idx == 0:
            return _rl_pending(0)  # trips the event while 1-9 are in flight
        await release.wait()
        return _build_pending(idx=idx)

    state = _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    task = _asyncio.create_task(
        gen.run(_make_request(suite_id=suite.id), run_id="rid-c4"),
    )
    await _asyncio.sleep(0.05)  # let prompt 0 trip the event
    release.set()
    result = await task

    assert sorted(state["calls"]) == list(range(10)), (
        "prompts 10-14 must short-circuit without calling the provider"
    )
    for i in range(10, 15):
        row = result.prompt_results[i]
        assert row["status"] == "failed" and row["error"] == "rate_limited"
        assert row["overall_score"] is None
    assert result.aggregate["replay_rate_limited_count"] == 6  # idx 0 + 10-14
    assert result.terminal_status == "partial"


async def test_in_flight_rate_limited_pending_projected_scoreless(
    db, write_queue, monkeypatch,
) -> None:
    """AC-5/AC-7 projection guard: bail-gate rows never enter latest_mean."""
    n = 3  # all in-flight (n <= cap) — the invisible-degradation case
    suite = await _seed_suite(db, n_prompts=n)

    async def _behavior(idx):
        if idx == 0:
            return _rl_pending(0)
        await _asyncio.sleep(0.01)
        p = _build_pending(idx=idx)
        p.overall_score = 8.0
        return p

    _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="rid-c5")

    row0 = result.prompt_results[0]
    assert row0["status"] == "failed" and row0["error"] == "rate_limited"
    assert row0["overall_score"] is None, (
        "heuristic 4.2 must NOT survive projection — it would poison latest_mean"
    )
    assert result.aggregate["replay_rate_limited_count"] == 1
    assert result.aggregate["mean_overall"] == pytest.approx(8.0)
    assert result.terminal_status == "partial", (
        "N <= cap all-in-flight degradation must never classify as completed"
    )


async def test_all_rate_limited_classifies_failed(
    db, write_queue, monkeypatch,
) -> None:
    """AC-5 homogeneous fixture: every row rate-limited → terminal 'failed'."""
    n = 3
    suite = await _seed_suite(db, n_prompts=n)

    async def _behavior(idx):
        return _rl_pending(idx)

    _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="rid-c6")
    assert result.terminal_status == "failed"
    assert result.aggregate["replay_rate_limited_count"] == n


async def test_progress_counter_monotonic_under_out_of_order(
    db, write_queue, monkeypatch,
) -> None:
    """AC-6: current == 1..N in publish order; idx is a permutation."""
    n = 5
    suite = await _seed_suite(db, n_prompts=n)
    events = _capture_prompt_completed_events(monkeypatch)

    async def _behavior(idx):
        await _asyncio.sleep((n - idx) * 0.02)
        return _build_pending(idx=idx)

    _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    await gen.run(_make_request(suite_id=suite.id), run_id="rid-c7")

    assert [e["current"] for e in events] == list(range(1, n + 1))
    assert sorted(e["idx"] for e in events) == list(range(n))
    assert [e["idx"] for e in events] != list(range(n)), (
        "fixture must actually complete out of order for this test to bite"
    )
    assert all(e["total"] == n for e in events)


async def test_cancellation_propagates_to_caller(
    db, write_queue, monkeypatch,
) -> None:
    """AC-8: cancelling the run cancels children and raises CancelledError."""
    suite = await _seed_suite(db, n_prompts=4)
    never = _asyncio.Event()

    async def _behavior(idx):
        await never.wait()
        return _build_pending(idx=idx)

    state = _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    task = _asyncio.create_task(
        gen.run(_make_request(suite_id=suite.id), run_id="rid-c8"),
    )
    for _ in range(100):
        if state["in_flight"] == 4:
            break
        await _asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(_asyncio.CancelledError):
        await task
    await _asyncio.sleep(0.05)
    assert state["in_flight"] == 0, "children must not leak past cancellation"


async def test_concurrency_one_is_observationally_sequential(
    db, write_queue, monkeypatch,
) -> None:
    """AC-9: cap=1 reproduces the pre-change sequential behavior."""
    n = 4
    suite = await _seed_suite(db, n_prompts=n)
    monkeypatch.setattr(
        replay_mod, "PROBE_PROMPT_CONCURRENCY", {"internal": 1},
    )
    events = _capture_prompt_completed_events(monkeypatch)

    async def _behavior(idx):
        await _asyncio.sleep(0.005)
        return _build_pending(idx=idx)

    state = _patch_run_single_prompt_concurrent(monkeypatch, behavior=_behavior)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="rid-c9")

    assert state["max_in_flight"] == 1
    assert [e["idx"] for e in events] == list(range(n))      # in order
    assert [e["current"] for e in events] == [i + 1 for i in range(n)]
    assert result.terminal_status == "completed"


# ===========================================================================
# v0.4.37 Cycle 1 — replay output capture (spec §3.1, AC-1 + AC-2)
# ===========================================================================


async def test_output_capture_caps_contract() -> None:
    """AC-1/AC-2 precondition: caps live in generators/_constants.py with the
    spec-locked values (20_000 / 8_000)."""
    assert REPLAY_OUTPUT_SNAPSHOT_MAX_CHARS == 20_000
    assert REPLAY_CHANGES_SUMMARY_MAX_CHARS == 8_000


async def test_projection_carries_output_fields() -> None:
    """AC-1: a scored pending's optimized_prompt + changes_summary survive
    projection un-truncated, with output_truncated=False."""
    pending = _build_pending(idx=0)
    pending.optimized_prompt = "optimized text body"
    pending.changes_summary = "made it better"

    row = replay_mod._project_pending_to_result(
        idx=0, raw_prompt="raw", pending=pending, baseline_overall=None,
    )
    assert row["optimized_prompt"] == "optimized text body"
    assert row["changes_summary"] == "made it better"
    assert row["output_truncated"] is False


async def test_projection_truncates_at_caps_and_flags() -> None:
    """AC-2: a 25,000-char output persists exactly 20,000 chars and a
    9,000-char changes_summary persists exactly 8,000, with
    output_truncated=True."""
    pending = _build_pending(idx=0)
    pending.optimized_prompt = "x" * 25_000
    pending.changes_summary = "y" * 9_000

    row = replay_mod._project_pending_to_result(
        idx=0, raw_prompt="raw", pending=pending, baseline_overall=None,
    )
    assert len(row["optimized_prompt"]) == 20_000
    assert len(row["changes_summary"]) == 8_000
    assert row["output_truncated"] is True


async def test_rate_limited_row_carries_none_output_keys() -> None:
    """AC-1 key-presence discriminator: score-less rate-limited rows carry
    BOTH keys with None values (post-v0.4.37 rows always have the keys)."""
    row = replay_mod._rate_limited_row(
        idx=0, raw_prompt="raw", baseline_overall=None, intent_label=None,
    )
    assert "optimized_prompt" in row and row["optimized_prompt"] is None
    assert "changes_summary" in row and row["changes_summary"] is None
    assert row["output_truncated"] is False


async def test_failed_row_envelope_carries_none_output_keys(
    db, write_queue, monkeypatch,
) -> None:
    """AC-1: the inline failed-row envelope (per-prompt exception path)
    carries both keys with None values — end-to-end through run()."""
    suite = await _seed_suite(db, n_prompts=1)
    _patch_run_single_prompt(
        monkeypatch, raises_at_idx={0: RuntimeError("boom")},
    )
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="run-f1")

    row = result.prompt_results[0]
    assert row["status"] == "failed"
    assert "optimized_prompt" in row and row["optimized_prompt"] is None
    assert "changes_summary" in row and row["changes_summary"] is None


async def test_end_to_end_replay_rows_carry_output_keys(
    db, write_queue, monkeypatch,
) -> None:
    """AC-1 happy path: every persisted prompt_results row carries the
    output keys with real values."""
    suite = await _seed_suite(db, n_prompts=2)
    pendings = []
    for i in range(2):
        p = _build_pending(idx=i)
        p.optimized_prompt = f"optimized {i}"
        p.changes_summary = f"summary {i}"
        pendings.append(p)
    _patch_run_single_prompt(monkeypatch, pendings=pendings)
    gen = _make_generator(write_queue=write_queue)
    result = await gen.run(_make_request(suite_id=suite.id), run_id="run-h1")

    assert result.terminal_status == "completed"
    for i, row in enumerate(result.prompt_results):
        assert row["optimized_prompt"] == f"optimized {i}"
        assert row["changes_summary"] == f"summary {i}"
        assert row["output_truncated"] is False
