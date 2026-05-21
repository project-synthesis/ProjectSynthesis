"""Tests for content-negotiated SSE on POST /api/seed (v0.4.34).

Closes the last open Foundation P3 "out of scope" deferred item: Q3 Path 1
marked Accept-header SSE as "additive if needed". v0.4.34 ships it,
mirroring the canonical pattern already used by ``POST /api/probes``
(Foundation P3 Cycle 11) and ``POST /api/refine``.

Contract:
  - No ``Accept`` header (or ``Accept: */*``) → existing ``SeedOutput``
    JSON response, byte-identical to v0.4.33.
  - ``Accept: application/json`` → same JSON path.
  - ``Accept: text/event-stream`` → SSE stream emitting:
      * first event ``seed_started`` carrying the minted ``run_id``
      * zero or more ``seed_batch_progress`` events filtered to the
        request's ``run_id`` (cross-run events excluded)
      * terminal ``seed_completed`` OR ``seed_failed`` event closing
        the stream
    Each payload carries ``run_id`` for cross-channel correlation.

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md
      Q3 Path 1 ("additive if needed")
Plan: /home/drei/.claude/plans/twinkling-tinkering-snail.md Task 1
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators.base import GeneratorResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# SSE parsing helper (mirrors test_probe_router.py::_parse_sse)
# ---------------------------------------------------------------------------


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Split SSE response body into ``(event_name, data_dict)`` pairs.

    Tolerates both the spec-line ``event: <name>\\ndata: <json>`` form and
    the codebase ``data: {"event": "<name>", ...}`` single-line form so
    GREEN-phase implementations can use either.
    """
    out: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        ev: str | None = None
        data: dict | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[len("data: "):])
                except json.JSONDecodeError:
                    data = None
        if data is None:
            continue
        if ev is None and isinstance(data, dict) and "event" in data:
            ev = str(data["event"])
        if ev is not None:
            out.append((ev, data))
    return out


# ---------------------------------------------------------------------------
# Stub seed generator that publishes the canonical SSE event sequence to the
# event bus, threading run_id into every payload. Mirrors the seed_agent
# generator's _log_decision sites but routes through event_bus.publish so
# the router-side subscribe_for_run shim sees the same payloads it would
# in production once Item 4 GREEN ships the publish calls.
# ---------------------------------------------------------------------------


class _StubSeedSseGenerator:
    """Stub generator that publishes seed_started, seed_batch_progress,
    and a terminal seed_completed/seed_failed to event_bus.

    Used to drive the router's SSE branch without invoking the heavy
    SeedAgentGenerator pipeline. Each test parameterises the terminal
    status + prompts_optimized to cover the completed and failed
    terminator paths.
    """

    def __init__(
        self,
        terminal_status: str = "completed",
        prompts_optimized: int = 3,
        prompts_failed: int = 0,
        n_progress_events: int = 2,
        emit_other_run_progress: bool = False,
    ) -> None:
        self.terminal_status = terminal_status
        self.prompts_optimized = prompts_optimized
        self.prompts_failed = prompts_failed
        self.n_progress_events = n_progress_events
        self.emit_other_run_progress = emit_other_run_progress
        self.calls: list[tuple[Any, str]] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        # Yield once so the orchestrator's _create_row commit and SSE
        # consumer task get scheduled before the publish chain begins.
        await asyncio.sleep(0)

        completed = self.prompts_optimized
        failed = self.prompts_failed
        batch_id = str(uuid.uuid4())
        summary = (
            f"{completed} prompts optimized"
            f"{f', {failed} failed' if failed else ''}"
            f". 1 clusters created"
        )

        event_bus.publish("seed_started", {
            "run_id": run_id,
            "batch_id": batch_id,
            "tier": "passthrough",
            "prompt_count_target": completed + failed,
            "has_user_prompts": False,
        })

        # Optionally publish a progress event for a DIFFERENT run to
        # verify the per-run subscription filter excludes it.
        if self.emit_other_run_progress:
            event_bus.publish("seed_batch_progress", {
                "run_id": "other-run-not-mine",
                "batch_id": str(uuid.uuid4()),
                "completed": 1,
                "total": 5,
            })

        for i in range(self.n_progress_events):
            event_bus.publish("seed_batch_progress", {
                "run_id": run_id,
                "batch_id": batch_id,
                "completed": i + 1,
                "total": self.n_progress_events,
            })

        if self.terminal_status == "failed":
            event_bus.publish("seed_failed", {
                "run_id": run_id,
                "batch_id": batch_id,
                "phase": "optimize",
                "summary": "Stub failure",
            })
        else:
            event_bus.publish("seed_completed", {
                "run_id": run_id,
                "batch_id": batch_id,
                "status": self.terminal_status,
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "clusters_created": 1,
                "domains_touched": ["backend"],
                "summary": summary,
            })

        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=completed + failed,
            prompt_results=[],
            aggregate={
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "summary": summary,
            },
            taxonomy_delta={
                "domains_touched": ["backend"],
                "clusters_created": 1,
            },
            final_report=None,
        )


# ---------------------------------------------------------------------------
# Fixtures — repoint session factory + install stub orchestrator
# (mirrors test_seed_router.py for parity)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def patched_orchestrator_session_factory(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    """Repoint ``app.database.async_session_factory`` at ``db_session``."""
    import app.database as database_mod

    class _SessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def _factory():
        return _SessionContext()

    monkeypatch.setattr(database_mod, "async_session_factory", _factory)
    yield _factory


@pytest_asyncio.fixture
async def stub_seed_sse_orchestrator(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Install a RunOrchestrator with the SSE-publishing stub generator."""
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubSeedSseGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": stub_gen, "topic_probe": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def stub_seed_sse_orchestrator_failed(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Install a RunOrchestrator with a stub that emits seed_failed."""
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubSeedSseGenerator(
        terminal_status="failed",
        prompts_optimized=0,
        prompts_failed=3,
    )
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": stub_gen, "topic_probe": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def stub_seed_sse_orchestrator_with_cross_run(
    app_client: AsyncClient,
    patched_orchestrator_session_factory: Any,
):
    """Stub orchestrator that ALSO emits a progress event for a different
    run_id mid-stream so the per-run filter can be verified."""
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubSeedSseGenerator(emit_other_run_progress=True)
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"seed_agent": stub_gen, "topic_probe": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


# ===========================================================================
# Tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Default-JSON contract preservation (Item 4 risk callout #1)
# ---------------------------------------------------------------------------


async def test_default_accept_header_returns_json(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator: Any,
) -> None:
    """No explicit Accept header → SeedOutput JSON (current contract).

    httpx sends ``Accept: */*`` by default. The router MUST route this to
    the synchronous JSON path, NOT the SSE branch — preserves v0.4.33
    behaviour byte-identically for browser clients, curl without -H, and
    any caller that doesn't opt into the new SSE surface.
    """
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": (
                "Test default accept header returns json shape"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
    )
    assert resp.status_code == 200
    # Response is JSON, not SSE
    content_type = resp.headers.get("content-type", "")
    assert "application/json" in content_type, (
        f"default Accept routed to SSE branch: content-type={content_type!r}"
    )
    body = resp.json()
    # SeedOutput shape preserved
    assert body["status"] in ("completed", "partial", "failed", "running")
    assert "run_id" in body
    assert isinstance(body.get("domains_touched"), list)
    assert isinstance(body.get("clusters_created"), int)


async def test_explicit_json_accept_returns_json(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator: Any,
) -> None:
    """Accept: application/json → SeedOutput JSON.

    Explicit JSON opt-in must also route to the sync JSON branch.
    """
    resp = await app_client.post(
        "/api/seed",
        json={
            "project_description": (
                "Test explicit json accept routes to sync"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "application/json" in content_type, (
        f"explicit JSON Accept routed to SSE: content-type={content_type!r}"
    )
    body = resp.json()
    assert body["status"] in ("completed", "partial", "failed", "running")
    assert "run_id" in body


# ---------------------------------------------------------------------------
# SSE branch — content-type + event sequence
# ---------------------------------------------------------------------------


async def test_event_stream_accept_returns_sse_response(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator: Any,
) -> None:
    """Accept: text/event-stream → StreamingResponse with SSE media type.

    The router MUST flip to the streaming branch when the client opts in
    via the canonical Accept header.
    """
    async with app_client.stream(
        "POST",
        "/api/seed",
        json={
            "project_description": (
                "Test event stream accept returns SSE"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" in content_type, (
            f"SSE Accept did not flip media_type: content-type={content_type!r}"
        )
        # Drain so the connection cleanly closes.
        await resp.aread()


async def test_sse_emits_seed_started_with_run_id(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator: Any,
) -> None:
    """First SSE event is ``seed_started`` carrying the minted ``run_id``.

    Race-free pattern verification: the router subscribes BEFORE the
    orchestrator dispatches, so the first published event (seed_started)
    is captured. If the subscription registered AFTER the orchestrator
    kicked off, seed_started could be missed → first event would be a
    later phase or the stream would terminate without seed_started.
    """
    async with app_client.stream(
        "POST",
        "/api/seed",
        json={
            "project_description": (
                "Test SSE first event is seed_started with run_id"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        text = await resp.aread()

    events = _parse_sse(text.decode())
    assert events, "no SSE events captured"
    first_name, first_payload = events[0]
    assert first_name == "seed_started", (
        f"first event was {first_name!r}, expected seed_started — "
        "subscription is racing the dispatch"
    )
    run_id = first_payload.get("run_id")
    assert isinstance(run_id, str) and len(run_id) >= 32, (
        f"seed_started missing run_id: {first_payload!r}"
    )


async def test_sse_filters_seed_batch_progress_by_run_id(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator_with_cross_run: Any,
) -> None:
    """seed_batch_progress for OTHER runs is NOT yielded into the stream.

    The router subscribes via ``event_bus.subscribe_for_run(run_id)``
    which filters events by their payload's ``run_id`` field. Cross-run
    progress events published during this request's lifetime MUST be
    excluded so a single client only sees events for its own run.
    """
    async with app_client.stream(
        "POST",
        "/api/seed",
        json={
            "project_description": (
                "Test SSE filters cross run progress events"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        text = await resp.aread()

    events = _parse_sse(text.decode())
    assert events, "no SSE events captured"
    # Identify this run's run_id from seed_started
    started = next(d for n, d in events if n == "seed_started")
    my_run_id = started["run_id"]

    # Every seed_batch_progress in the stream must be for OUR run
    progress_events = [d for n, d in events if n == "seed_batch_progress"]
    for prog in progress_events:
        assert prog.get("run_id") == my_run_id, (
            f"cross-run seed_batch_progress leaked into stream: {prog!r}"
        )
    # The stub did emit a cross-run progress event, but the filter
    # must have dropped it — so we must NOT see any with run_id="other-run-not-mine".
    leaked = [
        d for n, d in events
        if n == "seed_batch_progress" and d.get("run_id") == "other-run-not-mine"
    ]
    assert not leaked, (
        f"per-run subscription filter let foreign run_id through: {leaked!r}"
    )


async def test_sse_terminates_on_seed_completed_with_full_payload(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator: Any,
) -> None:
    """``seed_completed`` closes the stream; payload mirrors SeedOutput.

    Without a terminal event the SSE consumer's ``async for event in
    subscription`` never reaches break → stream hangs. The terminator
    contract is load-bearing.
    """
    async with app_client.stream(
        "POST",
        "/api/seed",
        json={
            "project_description": (
                "Test SSE terminates on seed_completed event"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        text = await resp.aread()

    events = _parse_sse(text.decode())
    names = [n for n, _ in events]
    assert "seed_started" in names
    assert "seed_completed" in names, (
        f"stream did not emit seed_completed terminator: names={names}"
    )
    # Terminal event MUST be last
    assert names[-1] == "seed_completed", (
        f"seed_completed was not the terminator: names={names}"
    )
    completed = next(d for n, d in events if n == "seed_completed")
    # Payload mirrors SeedOutput fields the frontend would consume
    for key in (
        "run_id",
        "status",
        "prompts_optimized",
        "prompts_failed",
        "clusters_created",
        "domains_touched",
        "summary",
    ):
        assert key in completed, (
            f"seed_completed payload missing {key}: {completed!r}"
        )


async def test_sse_terminates_on_seed_failed(
    app_client: AsyncClient,
    stub_seed_sse_orchestrator_failed: Any,
) -> None:
    """``seed_failed`` ALSO closes the stream (failure terminator).

    Two terminal events: seed_completed and seed_failed. Either must end
    the SSE consumer's loop or the stream hangs forever on the client.
    """
    async with app_client.stream(
        "POST",
        "/api/seed",
        json={
            "project_description": (
                "Test SSE terminates on seed_failed event"
            ).ljust(40, "x"),
            "prompt_count": 5,
        },
        headers={"Accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        text = await resp.aread()

    events = _parse_sse(text.decode())
    names = [n for n, _ in events]
    assert "seed_started" in names
    assert "seed_failed" in names, (
        f"stream did not emit seed_failed terminator: names={names}"
    )
    assert names[-1] == "seed_failed", (
        f"seed_failed was not the terminator: names={names}"
    )
    failed = next(d for n, d in events if n == "seed_failed")
    assert "run_id" in failed
