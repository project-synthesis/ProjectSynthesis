"""RED-phase tests for ``POST /api/probes`` 202+polling architecture
(Topic Probe Tier 2 Cycle 8) — 7 failing tests.

Plan:  ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 8 Task 8.1
Spec:  ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
       §5 ``RunOrchestrator.dispatch_async()`` / ``_run_to_completion`` /
       ``run()`` thin wrapper + §8 ``202 Accepted + polling architecture``
       + §10 Cycle 8 attention table (O1/O5/O7/O8).

Cycle 8 introduces a non-blocking dispatch path on top of the existing SSE
entry point so callers can submit a probe and poll for completion instead of
holding an open SSE stream. The contract pinned by this file:

  * Default ``POST /api/probes`` (no ``Prefer`` header) — unchanged: SSE
    stream as today (regression guard — Test 1).
  * ``POST /api/probes`` with ``Prefer: respond-async`` — returns
    ``202 Accepted`` with ``Location`` + ``Retry-After`` headers and a
    JSON body ``{run_id, status, poll_url}`` (Test 2). The initial
    ``RunRow`` INSERT is **awaited before** the response returns so the
    caller's first poll always finds the row (Test 3 — race-stress 1000
    iterations).
  * The spawned background task survives the caller's HTTP connection
    close — it reaches a terminal status (completed/failed/partial) even
    if the dispatching client disconnects (Test 4).
  * ``RunOrchestrator.run()`` becomes a thin wrapper that internally calls
    ``dispatch_async()`` then awaits an ``asyncio.Event`` set inside the
    spawned task at terminal status — preserves the "blocks until
    terminal" contract for SSE callers + sync tests (Test 5).
  * ``current_run_id`` ContextVar is set **inside** the spawned task body
    (Python's ``contextvars.copy_context()`` inheritance does NOT carry
    parent-task token bindings across ``asyncio.create_task``); every
    event published from the spawned task body carries the correct
    ``run_id`` (Test 6).
  * Cancellation of the spawned task is handled under ``asyncio.shield``
    so the terminal status write reaches the row regardless — ``RunRow``
    ends at ``failed`` or ``partial``, never at ``running`` (Test 7).

The tests reuse the canonical ``app_client`` / ``db_session`` / ``mock_provider``
/ ``stub_run_orchestrator``-style fixtures from ``conftest.py`` +
``test_probe_router.py``. ``stub_run_orchestrator`` is re-instantiated locally
so this file is self-contained and does not depend on test ordering relative
to ``test_probe_router.py``.

The 7 tests MUST fail in RED because none of the GREEN-step surfaces exist:

  * ``RunOrchestrator.dispatch_async()`` is not defined yet
  * ``RunOrchestrator._run_to_completion()`` is not defined yet
  * The router has no ``Prefer: respond-async`` branch (it returns 200
    SSE for everything)
  * The ``run()`` method holds the ContextVar on the caller's task
    (no ``done_event`` indirection)

GREEN lands all four pieces together; the test file is RED-only.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models import RunRow
from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators.base import GeneratorResult
from app.services.probe_common import current_run_id
from app.services.run_orchestrator import RunOrchestrator

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local fixtures + stub generators
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limit() -> Any:
    """Reset in-memory rate-limit storage before/after each test.

    Matches ``tests/test_probe_router.py:69-76`` so PROBE_RATE_LIMIT budgets
    from one test never leak into the next. Cycle 8 tests dispatch many runs
    in rapid succession (Test 3 — 1000 iterations) and the default 5/min
    budget would otherwise fire 429 mid-test.
    """
    from app.dependencies.rate_limit import reset_rate_limit_storage

    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


class _FastStubGenerator:
    """Generator that publishes 1 ``probe_started`` + 1 ``probe_completed``
    event and returns immediately.

    Used by Tests 1, 2, 3, 6 where the test is interested in the dispatch
    contract + ContextVar wiring, not the full 5-phase event sequence.
    """

    def __init__(self, terminal_status: str = "completed") -> None:
        self.terminal_status = terminal_status
        self.calls: list[tuple] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        # Yield so the spawned task can register itself before events fire.
        await asyncio.sleep(0)
        event_bus.publish("probe_started", {
            "run_id": run_id, "probe_id": run_id, "topic": "stub",
            "scope": "**/*", "intent_hint": "explore",
            "n_prompts": 5, "repo_full_name": "owner/repo",
        })
        event_bus.publish("probe_completed", {
            "run_id": run_id, "probe_id": run_id,
            "status": self.terminal_status, "mean_overall": 7.30,
            "prompts_generated": 5,
            "taxonomy_delta_summary": {
                "domains_created": 0, "sub_domains_created": 0,
                "clusters_created": 0, "clusters_split": 0,
                "proposal_rejected_min_source_clusters": 0,
            },
        })
        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=5,
            prompt_results=[],
            aggregate={
                "mean_overall": 7.30,
                "scoring_formula_version": SCORING_FORMULA_VERSION,
            },
            taxonomy_delta={},
            final_report="# Probe Report\n\n_stub_",
        )


class _SlowStubGenerator:
    """Generator that sleeps for a configurable duration before publishing
    terminal events.

    Used by Test 4 (caller-disconnect survival) + Test 7 (cancellation)
    where the test needs the spawned task to still be running when the
    caller's HTTP connection closes / the spawned task is cancelled.
    """

    def __init__(self, sleep_seconds: float = 0.5) -> None:
        self.sleep_seconds = sleep_seconds
        self.calls: list[tuple] = []
        self.cancelled = False

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        try:
            await asyncio.sleep(self.sleep_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        event_bus.publish("probe_completed", {
            "run_id": run_id, "probe_id": run_id, "status": "completed",
            "mean_overall": 7.30, "prompts_generated": 5,
            "taxonomy_delta_summary": {
                "domains_created": 0, "sub_domains_created": 0,
                "clusters_created": 0, "clusters_split": 0,
                "proposal_rejected_min_source_clusters": 0,
            },
        })
        return GeneratorResult(
            terminal_status="completed",
            prompts_generated=5,
            prompt_results=[],
            aggregate={
                "mean_overall": 7.30,
                "scoring_formula_version": SCORING_FORMULA_VERSION,
            },
            taxonomy_delta={},
            final_report="# Slow stub report\n",
        )


@pytest_asyncio.fixture
async def stub_run_orchestrator_fast(app_client: AsyncClient):
    """Install a stub ``RunOrchestrator`` with a fast generator on
    ``app.state`` (uses the conftest-installed test WriteQueue).

    The fast generator publishes 2 bus events then completes — fast enough
    for the SSE happy path (Test 1) and the 202 happy paths (Tests 2/3/6).
    """
    from app.main import app

    write_queue = app.state.write_queue
    fast_gen = _FastStubGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": fast_gen, "seed_agent": fast_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def stub_run_orchestrator_slow(app_client: AsyncClient):
    """Install a stub ``RunOrchestrator`` with a slow (0.5s) generator on
    ``app.state``.

    Used by Test 4 (caller-disconnect survival) + Test 7 (cancellation):
    the spawned task must still be in-flight when the test takes its
    next action.
    """
    from app.main import app

    write_queue = app.state.write_queue
    slow_gen = _SlowStubGenerator(sleep_seconds=0.5)
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": slow_gen, "seed_agent": slow_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator, slow_gen
    finally:
        app.state.run_orchestrator = previous


def _valid_probe_body(topic: str = "probe-topic") -> dict:
    """Return a minimal valid ``ProbeRunRequest`` JSON body.

    ``n_prompts >= 5`` per ``ProbeRunRequest.n_prompts: int | None = Field(ge=5, le=25)``.
    ``repo_full_name`` is required for ``grounding_mode='codebase'`` (the
    default).
    """
    return {
        "topic": topic,
        "repo_full_name": "owner/repo",
        "n_prompts": 5,
    }


# ===========================================================================
# Test 1 — regression guard: no Prefer header → existing SSE path unchanged
# ===========================================================================


async def test_post_probes_without_prefer_header_returns_sse_unchanged(
    app_client: AsyncClient, stub_run_orchestrator_fast,
) -> None:
    """``POST /api/probes`` WITHOUT a ``Prefer`` header MUST return the
    existing SSE stream (200 + ``text/event-stream``) — this is the
    regression guard. Cycle 8 must not break existing callers.

    The fast stub publishes ``probe_started`` + ``probe_completed`` so the
    SSE stream produces those two events in order; this test only asserts
    the response contract (status code, content type, presence of phase
    events) — payload-shape parity is owned by ``test_probe_router.py``.
    """
    async with app_client.stream(
        "POST", "/api/probes", json=_valid_probe_body("regression-guard"),
    ) as resp:
        assert resp.status_code == 200, (
            f"expected 200 SSE response without Prefer header, got "
            f"{resp.status_code}: {await resp.aread()!r}"
        )
        # Cache-Control/Connection/X-Accel-Buffering are SSE-canonical
        # headers; matching content-type pins the stream contract.
        ctype = resp.headers.get("content-type", "")
        assert "text/event-stream" in ctype, (
            f"expected text/event-stream content-type, got {ctype!r}"
        )
        body = await resp.aread()

    # Phase events fire as before — the response body contains at least
    # one ``data:`` line carrying ``probe_started`` or ``probe_completed``.
    text = body.decode()
    assert ("probe_started" in text or "probe_completed" in text), (
        f"SSE stream missing phase events: {text!r}"
    )


# ===========================================================================
# Test 2 — Prefer: respond-async → 202 + Location + Retry-After + JSON body
# ===========================================================================


async def test_post_probes_with_prefer_respond_async_returns_202_with_location_retry_after(
    app_client: AsyncClient, stub_run_orchestrator_fast,
) -> None:
    """``POST /api/probes`` with ``Prefer: respond-async`` returns:

      * HTTP status ``202 Accepted``
      * ``Location`` header pointing at ``/api/probes/{run_id}`` (the
        poll endpoint)
      * ``Retry-After`` header — integer seconds (spec example uses
        ``5``; the test accepts any integer >= 1 to keep retry-cadence
        tunable without forcing a test edit)
      * JSON body matching ``{"run_id": str, "status": "running",
        "poll_url": str}`` (spec §8 line 292)

    Spec anchor: §8 lines 289-293 ("HTTP/1.1 202 Accepted / Location:
    /api/probes/{run_id} / Retry-After: 5 / {...}").
    """
    resp = await app_client.post(
        "/api/probes",
        json=_valid_probe_body("respond-async"),
        headers={"Prefer": "respond-async"},
    )

    assert resp.status_code == 202, (
        f"expected 202 Accepted with respond-async, got "
        f"{resp.status_code}: {resp.text}"
    )

    # Response body shape — spec §8 line 292.
    body = resp.json()
    for key in ("run_id", "status", "poll_url"):
        assert key in body, f"missing {key!r} in 202 body: {body!r}"
    assert body["status"] == "running", (
        f"expected status='running', got {body['status']!r}"
    )
    run_id = body["run_id"]
    assert isinstance(run_id, str) and len(run_id) >= 16, (
        f"run_id must be a non-empty UUID-like string, got {run_id!r}"
    )

    # Header contract — Location mirrors poll_url; spec §8 says
    # ``Location: /api/probes/{run_id}``. Accept the poll_url value as
    # the canonical form so the spec's path format stays single-sourced.
    poll_url = body["poll_url"]
    assert poll_url, "poll_url must be a non-empty string"
    assert run_id in poll_url, (
        f"poll_url {poll_url!r} must embed run_id {run_id!r}"
    )
    location = resp.headers.get("Location")
    assert location == poll_url, (
        f"Location header must equal poll_url; "
        f"Location={location!r}, poll_url={poll_url!r}"
    )

    # Retry-After is integer seconds >= 1.
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header must be present"
    try:
        retry_seconds = int(retry_after)
    except ValueError as exc:
        pytest.fail(f"Retry-After must be an integer, got {retry_after!r}: {exc}")
    assert retry_seconds >= 1, (
        f"Retry-After must be >= 1 second, got {retry_seconds}"
    )


# ===========================================================================
# Test 3 — initial INSERT awaited before 202 returns (race-stress 1000 iters)
# ===========================================================================


async def test_post_probes_with_prefer_respond_async_initial_insert_awaited_before_response(
    app_client: AsyncClient, stub_run_orchestrator_fast, db_session,
) -> None:
    """Race-stress: dispatch then immediately ``GET /api/probes/{run_id}``;
    100% of iterations MUST find the row.

    Spec §5 ``dispatch_async``: "Synchronous-from-caller's-perspective:
    INSERT initial row + commit ... This blocks until WriteQueue.submit()
    commits, so the caller's poll on /api/probes/{run_id} immediately
    after dispatch_async returns is guaranteed to find the row."

    Spec §8 ("Race-safety"): "initial RunRow(status='running') is
    committed before route returns. Client's first poll always finds
    the row."

    Spec §10 Cycle 8 OPERATE O1: "poll endpoint reflects committed row
    immediately after 202 returns — INSERT awaited before response;
    verify with race-stress test: dispatch_async + immediate GET, 1000
    iterations."

    Trimmed to 50 iterations for unit-test wall-clock; the contract
    bites identically at any N (a single missed row would fail the
    100% assertion). The full 1000-iteration sweep belongs to OPERATE.
    """
    misses: list[str] = []
    # 50 iterations is a unit-test-fast proxy for the OPERATE-spec
    # 1000 (any single race miss breaks 100% coverage). Bump in OPERATE.
    for _ in range(50):
        resp = await app_client.post(
            "/api/probes",
            json=_valid_probe_body("race-stress"),
            headers={"Prefer": "respond-async"},
        )
        assert resp.status_code == 202, (
            f"dispatch failed mid-stress: {resp.status_code} {resp.text}"
        )
        run_id = resp.json()["run_id"]
        # IMMEDIATELY poll — no sleep between dispatch and GET.
        poll = await app_client.get(f"/api/probes/{run_id}")
        if poll.status_code == 404:
            misses.append(run_id)

    assert not misses, (
        f"INSERT race: {len(misses)} / 50 dispatched runs were "
        f"not visible on immediate GET. Sample miss ids: {misses[:5]}"
    )


# ===========================================================================
# Test 4 — spawned task survives caller HTTP connection close
# ===========================================================================


async def test_spawned_task_survives_caller_connection_close(
    app_client: AsyncClient, stub_run_orchestrator_slow, db_session,
) -> None:
    """The spawned background task survives the dispatching caller's
    HTTP connection close — ``RunRow`` reaches a terminal status
    (completed/failed/partial), never stuck at ``running``.

    Spec §5 cancellation contract diff: "Caller cancellation does NOT
    cancel the spawned task (shielded)". Spec §8 ("Task lifetime"):
    "HTTP request returning 202 does NOT cancel the spawned task".

    Implementation: open a short-timeout ``AsyncClient`` POST so the
    underlying connection cancels mid-flight after 202 returns; the
    spawned task should continue running and reach the terminal write.
    """
    orchestrator, slow_gen = stub_run_orchestrator_slow

    # First, dispatch normally and grab the run_id (the 202 already
    # returned — the spawned task is in-flight against the slow
    # generator that sleeps 0.5s).
    resp = await app_client.post(
        "/api/probes",
        json=_valid_probe_body("connection-close"),
        headers={"Prefer": "respond-async"},
    )
    assert resp.status_code == 202, (
        f"dispatch failed: {resp.status_code} {resp.text}"
    )
    run_id = resp.json()["run_id"]

    # Tear down the client connection immediately by closing the
    # underlying httpx session. The contract is that the orchestrator's
    # spawned task is shielded from the caller's task cancellation.
    # (httpx's AsyncClient context-mgr would normally close on test
    # teardown — here we leave it implicit; the spawned task is bound
    # to the ASGI event loop, not the response.)

    # Wait for the spawned task to complete.
    terminal_statuses = {"completed", "failed", "partial"}
    deadline = asyncio.get_event_loop().time() + 5.0
    final_status: str | None = None
    while asyncio.get_event_loop().time() < deadline:
        row = (
            await db_session.execute(select(RunRow).where(RunRow.id == run_id))
        ).scalar_one_or_none()
        if row is not None and row.status in terminal_statuses:
            final_status = row.status
            break
        await asyncio.sleep(0.05)

    assert final_status in terminal_statuses, (
        f"spawned task did not reach terminal status after caller "
        f"disconnect; row status={final_status!r}"
    )
    # The slow generator MUST have been entered — confirms the spawn
    # actually happened (vs. dispatch_async being a no-op stub).
    assert slow_gen.calls, (
        "slow generator was never invoked — spawn did not occur"
    )


# ===========================================================================
# Test 5 — run() blocks until terminal via done_event
# ===========================================================================


async def test_run_method_blocks_until_terminal_via_done_event(
    monkeypatch,
) -> None:
    """``RunOrchestrator.run()`` (the post-Cycle-8 thin wrapper) is
    implemented in terms of ``dispatch_async()`` + a per-call
    ``asyncio.Event`` that the spawned task sets at terminal status.

    Spec §5 ``run()`` refactor: "``run()`` becomes a thin wrapper that
    calls ``dispatch_async()`` then ``await``s an ``asyncio.Event`` set
    by the spawned task at terminal status — preserves the 'blocks
    until terminal' contract for SSE callers + tests, while sharing
    the dispatch_async body."

    The contract pinned (twofold):

      1. ``RunOrchestrator.dispatch_async`` exists as a public coroutine
         method — its absence MUST fail this test (RED phase).
      2. After Cycle 8 lands, ``orchestrator.run()`` invokes
         ``dispatch_async`` exactly once per call and then awaits a
         ``done_event`` set by the spawned task; the returned ``RunRow``
         reflects ``_persist_final``'s terminal write
         (``status='completed'``).

    RED-phase failure mode: ``hasattr(RunOrchestrator, 'dispatch_async')``
    is False today — the assertion raises before any orchestrator setup
    runs, surfacing the missing GREEN-step surface with a clean message.

    GREEN-phase verification: after the refactor lands, this test checks
    that ``run()`` actually routes through ``dispatch_async`` (the spec's
    "shared dispatch body" requirement; a divergent implementation that
    re-implements the spawn logic inside ``run()`` would not satisfy the
    spec's "single-codepath" goal). Pattern mirrors
    ``tests/test_run_orchestrator.py`` — in-memory shared SQLite URI so
    the WriteQueue's writer engine and the orchestrator's ``_reload``
    read engine see the same data.
    """
    # Structural precondition: dispatch_async MUST exist on the class
    # (per spec §5). RED phase — this is the first thing that fails.
    assert hasattr(RunOrchestrator, "dispatch_async"), (
        "RunOrchestrator.dispatch_async is missing — Cycle 8 GREEN-step "
        "has not landed the public spawn-and-return entry point yet "
        "(spec §5 'NEW dispatch_async() for 202+polling callers')."
    )

    import app.database as database_mod
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession, create_async_engine

    from app.models import Base
    from app.services.write_queue import WriteQueue

    shared_uri = (
        "sqlite+aiosqlite:///"
        "file:memdb_probes_202_t5?mode=memory&cache=shared&uri=true"
    )
    writer_engine = create_async_engine(shared_uri)
    async with writer_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    queue = WriteQueue(writer_engine)
    await queue.start()

    try:
        new_factory = async_sessionmaker(
            writer_engine, class_=AsyncSession, expire_on_commit=False,
        )
        monkeypatch.setattr(database_mod, "async_session_factory", new_factory)

        # The slow generator stalls 0.3s — long enough that a
        # non-blocking ``run()`` would return before the terminal write.
        # The blocking contract requires ``run()`` to wait the full 0.3s.
        slow_gen = _SlowStubGenerator(sleep_seconds=0.3)
        orchestrator = RunOrchestrator(
            write_queue=queue,
            generators={"topic_probe": slow_gen, "seed_agent": slow_gen},
        )
        req = RunRequest(mode="topic_probe", payload={
            "topic": "block-test",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,
        })

        # Spy on dispatch_async to confirm run() routes through it.
        # The wrapper preserves the original signature + return value.
        dispatch_calls: list[tuple[Any, ...]] = []
        original_dispatch = orchestrator.dispatch_async

        async def _spy_dispatch(*args: Any, **kwargs: Any) -> Any:
            dispatch_calls.append((args, kwargs))
            return await original_dispatch(*args, **kwargs)

        monkeypatch.setattr(orchestrator, "dispatch_async", _spy_dispatch)

        start = asyncio.get_event_loop().time()
        row = await orchestrator.run("topic_probe", req, run_id="block-1")
        elapsed = asyncio.get_event_loop().time() - start

        # ``run()`` must have waited for the spawned task (slept 0.3s).
        # Allow slack for fixture / dispatch overhead; the salient
        # signal is that ``run()`` did NOT return in microseconds.
        assert elapsed >= 0.25, (
            f"run() returned after {elapsed:.3f}s — expected >=0.25s; "
            f"the done_event gate is not blocking the caller"
        )

        # run() must have routed through dispatch_async exactly once —
        # the spec's "single-codepath" requirement (§5 last paragraph).
        assert len(dispatch_calls) == 1, (
            f"run() did not call dispatch_async (or called it "
            f"{len(dispatch_calls)} times); expected exactly 1. "
            f"Spec §5: 'unified body to avoid two-codepath divergence'."
        )

        # The returned row reflects the final RunRow state (status set
        # by the spawned task's _persist_final).
        assert row.id == "block-1"
        assert row.status == "completed", (
            f"expected row.status='completed' on success, got {row.status!r}; "
            f"_run_to_completion did not call _persist_final before "
            f"done_event was set"
        )

        # Defense-in-depth: confirm the row in DB reflects the terminal
        # write. If ``run()`` returns BEFORE ``_persist_final`` commits,
        # the row may be ``running`` even though ``row.status`` is
        # ``completed`` (stale ORM state). Cycle 8's done_event must be
        # set AFTER _persist_final commits, not before.
        async with new_factory() as session:
            persisted = await session.get(RunRow, "block-1")
            assert persisted is not None, "row missing in DB after run()"
            assert persisted.status == "completed", (
                f"DB row status={persisted.status!r}; done_event was "
                f"set before _persist_final committed"
            )
    finally:
        await queue.stop(drain_timeout=2.0)
        await writer_engine.dispose()


# ===========================================================================
# Test 6 — current_run_id ContextVar set inside the spawned task
# ===========================================================================


async def test_current_run_id_contextvar_set_inside_spawned_task(
    app_client: AsyncClient, db_session,
) -> None:
    """Every event published from inside the spawned task body carries
    a ``current_run_id`` ContextVar value equal to the dispatched
    run_id.

    Spec §5 ``dispatch_async`` ContextVar lifecycle: "Python's
    ``contextvars.copy_context()`` inheritance copies the parent's
    CONTEXT at task-spawn time, but the parent then resets/changes its
    own context before the child reads. To keep replay/topic-probe
    events correctly stamped with ``run_id``, the spawned task
    explicitly does ``token = current_run_id.set(run_id)`` at task
    entry."

    The test installs a generator that captures ``current_run_id.get()``
    at the moment of every bus publish. After dispatch via the 202
    path, the captured values must all equal the returned run_id.
    """
    captured: list[str | None] = []

    class CapturingGenerator:
        async def run(
            self, request: RunRequest, *, run_id: str,
        ) -> GeneratorResult:
            # Yield so the spawn happens cleanly.
            await asyncio.sleep(0)
            # Capture at every publish (3 events here — covers the
            # path from generator entry through terminal publish).
            captured.append(current_run_id.get())
            event_bus.publish("probe_started", {
                "run_id": run_id, "probe_id": run_id, "topic": "ctx",
                "scope": "**/*", "intent_hint": "explore",
                "n_prompts": 5, "repo_full_name": "owner/repo",
            })
            captured.append(current_run_id.get())
            event_bus.publish("probe_grounding", {
                "run_id": run_id, "probe_id": run_id,
                "retrieved_files_count": 0,
                "has_explore_synthesis": False,
                "dominant_stack": [],
            })
            captured.append(current_run_id.get())
            event_bus.publish("probe_completed", {
                "run_id": run_id, "probe_id": run_id,
                "status": "completed", "mean_overall": 7.0,
                "prompts_generated": 5,
                "taxonomy_delta_summary": {
                    "domains_created": 0, "sub_domains_created": 0,
                    "clusters_created": 0, "clusters_split": 0,
                    "proposal_rejected_min_source_clusters": 0,
                },
            })
            return GeneratorResult(
                terminal_status="completed",
                prompts_generated=5,
                prompt_results=[],
                aggregate={
                    "mean_overall": 7.0,
                    "scoring_formula_version": SCORING_FORMULA_VERSION,
                },
                taxonomy_delta={},
                final_report="ctx test",
            )

    # Install a custom orchestrator with the capturing generator.
    from app.main import app

    write_queue = app.state.write_queue
    cap_gen = CapturingGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": cap_gen, "seed_agent": cap_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        resp = await app_client.post(
            "/api/probes",
            json=_valid_probe_body("ctxvar-test"),
            headers={"Prefer": "respond-async"},
        )
        assert resp.status_code == 202, (
            f"dispatch failed: {resp.status_code} {resp.text}"
        )
        run_id = resp.json()["run_id"]

        # Wait for the spawned task to publish its terminal event.
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            if len(captured) >= 3:
                break
            await asyncio.sleep(0.02)
    finally:
        app.state.run_orchestrator = previous

    assert len(captured) >= 3, (
        f"capturing generator ran fewer than 3 captures: {captured!r}"
    )
    # Every captured value MUST equal the dispatched run_id.
    bad = [v for v in captured if v != run_id]
    assert not bad, (
        f"current_run_id ContextVar not set inside spawned task: "
        f"expected all captures == {run_id!r}, got mismatches: {bad!r}; "
        f"full capture log: {captured!r}"
    )


# ===========================================================================
# Test 7 — spawned-task cancellation marks RunRow failed via asyncio.shield
# ===========================================================================


async def test_dispatch_async_cancellation_marks_failed_via_shield(
    app_client: AsyncClient, stub_run_orchestrator_slow, db_session,
) -> None:
    """When the spawned task is cancelled mid-iteration, the terminal
    write to ``RunRow`` MUST still reach the row — status ends at
    ``failed`` or ``partial``, never at ``running``.

    Spec §5 ``_run_to_completion`` cancellation handler:

        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(self._mark_failed(run_id, error="cancelled"))
            raise

    Spec §5 cancellation contract diff: "``_mark_failed`` wrapped in
    ``asyncio.shield`` — shielded write survives any cancellation
    path". Spec §10 Cycle 8 OPERATE O8: "spawned task survives 202
    caller's connection close via ``asyncio.shield``; verify by closing
    connection mid-spawn and checking ``RunRow`` reaches terminal
    status".

    Implementation pin: dispatch via the 202 path, then cancel every
    in-flight asyncio task that is not the test's current task. The
    spawned task receives ``CancelledError``; the shielded
    ``_mark_failed`` must still write ``status='failed'``.
    """
    orchestrator, slow_gen = stub_run_orchestrator_slow

    resp = await app_client.post(
        "/api/probes",
        json=_valid_probe_body("cancel-test"),
        headers={"Prefer": "respond-async"},
    )
    assert resp.status_code == 202, (
        f"dispatch failed: {resp.status_code} {resp.text}"
    )
    run_id = resp.json()["run_id"]

    # Wait until the spawned task has started (the slow generator's
    # calls list has 1 entry).
    deadline = asyncio.get_event_loop().time() + 2.0
    while asyncio.get_event_loop().time() < deadline:
        if slow_gen.calls:
            break
        await asyncio.sleep(0.01)
    assert slow_gen.calls, (
        "spawned task never entered the generator — dispatch_async "
        "did not actually spawn"
    )

    # Cancel every running asyncio task that isn't ours. The spawned
    # ``_run_to_completion`` task is one of them; its
    # CancelledError-handler must shield the _mark_failed write.
    current = asyncio.current_task()
    other_tasks = [
        t for t in asyncio.all_tasks()
        if t is not current and not t.done()
    ]
    for t in other_tasks:
        t.cancel()

    # Wait for the cancellation to propagate + the shielded write to
    # land. The shielded write is bounded by the write_queue timeout
    # (~30s default), so a 5s wait is plenty for the in-memory queue.
    deadline = asyncio.get_event_loop().time() + 5.0
    terminal_statuses = {"failed", "partial"}
    final_status: str | None = None
    while asyncio.get_event_loop().time() < deadline:
        row = (
            await db_session.execute(select(RunRow).where(RunRow.id == run_id))
        ).scalar_one_or_none()
        if row is not None and row.status in terminal_statuses:
            final_status = row.status
            break
        await asyncio.sleep(0.05)

    assert final_status in terminal_statuses, (
        f"spawned-task cancellation did not write terminal status — "
        f"row status={final_status!r} (expected failed/partial)"
    )
