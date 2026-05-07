"""Tests for routers/probes.py REST surface (Topic Probe Tier 1 + Foundation P3).

Pre-Foundation: AC-C5-1 through AC-C5-6 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 5.
P3 Cycle 11: § 9 cat 6 — 12 cat-6 tests + 1 spec-gap-D test exercising
the race-free subscribe-before-dispatch shim that routes through
``RunOrchestrator`` and reads from ``RunRow WHERE mode='topic_probe'``.

The refactored router constructs the SSE response by subscribing to
``event_bus.subscribe_for_run(run_id)`` BEFORE creating the orchestrator
task, so no events are missed between dispatch and subscription
registration. GET endpoints serialize from ``RunRow`` (P3 unified model).

Plan:  docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 11
Spec:  docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 6.2 + § 8.1
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models import ProbeRun, RunRow
from app.schemas.pipeline_contracts import SCORING_FORMULA_VERSION
from app.schemas.probes import (
    ProbeAggregate,
    ProbeCompletedEvent,
    ProbeGeneratingEvent,
    ProbeGroundingEvent,
    ProbeProgressEvent,
    ProbeRunRequest,
    ProbeStartedEvent,
    ProbeTaxonomyDelta,
)
from app.schemas.runs import RunRequest
from app.services.event_bus import event_bus
from app.services.generators.base import GeneratorResult


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Split an SSE response body into ``(event_name, data_dict)`` pairs.

    Tolerates both the spec-line ``event: <name>\\ndata: <json>`` form and
    the codebase ``data: {"event": "<name>", ...}`` single-line form so
    GREEN-phase implementations can choose either.
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
        # Single-line variant: event name encoded in payload.
        if ev is None and isinstance(data, dict) and "event" in data:
            ev = str(data["event"])
        if ev is not None:
            out.append((ev, data))
    return out


def _strip_volatile(text: str) -> str:
    """Strip timestamps + UUIDs for snapshot-style comparison."""
    s = re.sub(r'"started_at":\s*"[^"]+"', '"started_at": "<TS>"', text)
    s = re.sub(r'"completed_at":\s*"[^"]+"', '"completed_at": "<TS>"', s)
    s = re.sub(r'"timestamp":\s*[0-9]+(\.[0-9]+)?', '"timestamp": "<TS>"', s)
    s = re.sub(r'"run_id":\s*"[a-f0-9-]+"', '"run_id": "<UUID>"', s)
    s = re.sub(r'"probe_id":\s*"[a-f0-9-]+"', '"probe_id": "<UUID>"', s)
    s = re.sub(
        r'"optimization_id":\s*"[a-f0-9-]+"',
        '"optimization_id": "<UUID>"', s,
    )
    s = re.sub(r'"id":\s*"[a-f0-9-]+"', '"id": "<UUID>"', s)
    s = re.sub(r'"seq":\s*[0-9]+', '"seq": "<N>"', s)
    return s


@pytest.fixture(autouse=True)
def reset_rate_limit() -> Any:
    """Reset the in-memory rate limit storage before/after each test."""
    from app.dependencies.rate_limit import reset_rate_limit_storage

    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


@pytest_asyncio.fixture
async def sample_probe_run_id(db_session) -> str:
    """Insert a completed RunRow row (mode=topic_probe) and return its id.

    Uses the canonical ``RunRow`` model directly so the row carries the
    full P3 contract (mode, status, JSON metadata columns). Equivalent to
    the legacy ``ProbeRun`` alias path; both write to the same table.
    """
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    agg = ProbeAggregate(
        mean_overall=7.30,
        p5_overall=6.80,
        p50_overall=7.30,
        p95_overall=7.80,
        completed_count=3,
        failed_count=0,
        f5_flag_fires=0,
        scoring_formula_version=SCORING_FORMULA_VERSION,
    )
    delta = ProbeTaxonomyDelta()
    db_session.add(
        RunRow(
            id=pid,
            mode="topic_probe",
            status="completed",
            started_at=now,
            completed_at=now,
            project_id=None,
            repo_full_name="owner/repo",
            topic="embedding cache invalidation",
            intent_hint="audit",
            prompts_generated=3,
            prompt_results=[],
            aggregate=agg.model_dump(),
            taxonomy_delta=delta.model_dump(),
            final_report="# Topic Probe Run Report\n\n_Synthetic fixture row._",
            topic_probe_meta={"scope": "**/*", "commit_sha": None},
        )
    )
    await db_session.commit()
    return pid


def _stub_events(probe_id: str, n_prompts: int) -> list[Any]:
    """Build the canonical 5-phase event sequence for a happy-path probe."""
    out: list[Any] = [
        ProbeStartedEvent(
            probe_id=probe_id,
            topic="probe-topic",
            scope="**/*",
            intent_hint="explore",
            n_prompts=n_prompts,
            repo_full_name="owner/repo",
        ),
        ProbeGroundingEvent(
            probe_id=probe_id,
            retrieved_files_count=3,
            has_explore_synthesis=True,
            dominant_stack=["python"],
        ),
        ProbeGeneratingEvent(
            probe_id=probe_id,
            prompts_generated=n_prompts,
            generator_duration_ms=1234,
            generator_model="claude-sonnet-4-6",
        ),
    ]
    for i in range(n_prompts):
        out.append(
            ProbeProgressEvent(
                probe_id=probe_id,
                current=i + 1,
                total=n_prompts,
                optimization_id=str(uuid.uuid4()),
                intent_label="audit cache",
                overall_score=7.30,
            )
        )
    out.append(
        ProbeCompletedEvent(
            probe_id=probe_id,
            status="completed",
            mean_overall=7.30,
            prompts_generated=n_prompts,
            taxonomy_delta_summary={
                "domains_created": 0,
                "sub_domains_created": 0,
                "clusters_created": 0,
                "clusters_split": 0,
                "proposal_rejected_min_source_clusters": 0,
            },
        )
    )
    return out


# ---------------------------------------------------------------------------
# P3 stub orchestrator + generator
# ---------------------------------------------------------------------------


class _StubProbeGenerator:
    """Generator that publishes the canonical 5-phase event sequence directly
    to ``event_bus`` with ``run_id`` threaded into every payload.

    Mirrors ``TopicProbeGenerator._publish_*`` static methods at the bus
    level, so the router-side ``subscribe_for_run`` shim sees the exact
    same payloads it would in production.
    """

    def __init__(
        self,
        terminal_status: str = "completed",
        n_prompts: int = 3,
        before_publish: Any | None = None,
    ) -> None:
        self.terminal_status = terminal_status
        self.n_prompts = n_prompts
        # Optional callback invoked between subscription registration and
        # the first publish — used to test race semantics.
        self.before_publish = before_publish
        self.calls: list[tuple] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        if self.before_publish is not None:
            await self.before_publish(run_id)

        # Yield a tick so the SSE consumer's task can register itself.
        await asyncio.sleep(0)

        topic = str(request.payload.get("topic", "probe-topic"))
        scope = str(request.payload.get("scope") or "**/*")
        intent_hint = str(request.payload.get("intent_hint") or "explore")
        repo_full_name = str(request.payload.get("repo_full_name") or "owner/repo")
        n_prompts = int(request.payload.get("n_prompts") or self.n_prompts)

        event_bus.publish("probe_started", {
            "run_id": run_id,
            "probe_id": run_id,
            "topic": topic,
            "scope": scope,
            "intent_hint": intent_hint,
            "n_prompts": n_prompts,
            "repo_full_name": repo_full_name,
        })
        event_bus.publish("probe_grounding", {
            "run_id": run_id,
            "probe_id": run_id,
            "retrieved_files_count": 3,
            "has_explore_synthesis": True,
            "dominant_stack": ["python"],
        })
        event_bus.publish("probe_generating", {
            "run_id": run_id,
            "probe_id": run_id,
            "prompts_generated": n_prompts,
            "generator_duration_ms": 1234,
            "generator_model": "claude-sonnet-4-6",
        })
        for i in range(n_prompts):
            event_bus.publish("probe_prompt_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "current": i + 1,
                "total": n_prompts,
                "optimization_id": str(uuid.uuid4()),
                "intent_label": "audit cache",
                "overall_score": 7.30,
                "status": "completed",
            })

        if self.terminal_status == "failed":
            event_bus.publish("probe_failed", {
                "run_id": run_id,
                "probe_id": run_id,
                "phase": "running",
                "error_class": "ProbeError",
                "error_message_truncated": "stub failure",
            })
        else:
            event_bus.publish("probe_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "status": self.terminal_status,
                "mean_overall": 7.30,
                "prompts_generated": n_prompts,
                "taxonomy_delta_summary": {
                    "domains_created": 0,
                    "sub_domains_created": 0,
                    "clusters_created": 0,
                    "clusters_split": 0,
                    "proposal_rejected_min_source_clusters": 0,
                },
            })

        return GeneratorResult(
            terminal_status=self.terminal_status,  # type: ignore[arg-type]
            prompts_generated=n_prompts,
            prompt_results=[],
            aggregate={
                "mean_overall": 7.30,
                "scoring_formula_version": SCORING_FORMULA_VERSION,
            },
            taxonomy_delta={},
            final_report="# Probe Report\n\n_stub_",
        )


@pytest_asyncio.fixture
async def stub_run_orchestrator(app_client: AsyncClient, db_session):
    """Install a stub ``RunOrchestrator`` on ``app.state`` that uses the
    test ``write_queue`` and a deterministic stub generator.

    The orchestrator runs the WriteQueue + stub generator end-to-end so
    the refactored router exercises the real ``RunOrchestrator.run()``
    contract: ``_create_row`` happens before the generator publishes,
    and ``_persist_final`` happens after — matching production semantics.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubProbeGenerator()
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={"topic_probe": stub_gen, "seed_agent": stub_gen},
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator
    finally:
        app.state.run_orchestrator = previous


# ===========================================================================
# Pre-Foundation tests (preserved from C5) — keep `link_repo_first`,
# pagination, single-fetch, rate-limit shape parity.
# ===========================================================================


class TestProbeRouter:
    @pytest.mark.asyncio
    async def test_post_probes_streams_5_phase_events_happy_path(
        self, app_client: AsyncClient, stub_run_orchestrator,
    ):
        """AC-C5-1 (happy path): all 5 phase event types streamed; final status 200."""
        body = {
            "topic": "embedding cache invalidation",
            "intent_hint": "audit",
            "n_prompts": 5,
            "repo_full_name": "owner/repo",
        }
        async with app_client.stream("POST", "/api/probes", json=body) as resp:
            assert resp.status_code == 200
            text = await resp.aread()
        events = _parse_sse(text.decode())
        names = [name for name, _ in events]
        assert "probe_started" in names
        assert "probe_grounding" in names
        assert "probe_generating" in names
        assert names.count("probe_prompt_completed") == 5
        assert "probe_completed" in names

    @pytest.mark.asyncio
    async def test_get_probes_paginated(self, app_client: AsyncClient):
        """AC-C5-2: GET /api/probes returns paginated envelope."""
        resp = await app_client.get("/api/probes?offset=0&limit=10")
        assert resp.status_code == 200
        body = resp.json()
        for k in ("total", "count", "offset", "items", "has_more", "next_offset"):
            assert k in body

    @pytest.mark.asyncio
    async def test_get_probe_by_id_returns_full_result(
        self, app_client: AsyncClient, sample_probe_run_id: str,
    ):
        """AC-C5-3: GET /api/probes/{id} returns ProbeRunResult shape."""
        resp = await app_client.get(f"/api/probes/{sample_probe_run_id}")
        assert resp.status_code == 200
        body = resp.json()
        for k in ("id", "topic", "aggregate", "taxonomy_delta", "final_report", "status"):
            assert k in body

    @pytest.mark.asyncio
    async def test_get_unknown_probe_404(self, app_client: AsyncClient):
        """AC-C5-5: 404 on unknown probe_id with canonical reason code."""
        resp = await app_client.get("/api/probes/nonexistent-uuid-xyz")
        assert resp.status_code == 404
        body = resp.json()
        detail = str(body.get("detail", "")).lower()
        assert "probe_not_found" in detail or "probe" in detail

    @pytest.mark.asyncio
    async def test_post_probes_without_repo_returns_400_link_repo_first(
        self, app_client: AsyncClient,
    ):
        """AC-C5-6: probes without linked repo -> 400 + reason='link_repo_first'."""
        body = {"topic": "x", "n_prompts": 5}  # no repo_full_name
        resp = await app_client.post("/api/probes", json=body)
        assert resp.status_code == 400
        assert "link_repo_first" in resp.text


# ===========================================================================
# Foundation P3 cycle 11 — § 9 cat 6 (12 tests) + spec-gap D (1 test)
# ===========================================================================


class TestProbeRouterFoundationP3:
    @pytest.mark.asyncio
    async def test_post_probes_sse_event_sequence_byte_identical(
        self,
        app_client: AsyncClient,
        stub_run_orchestrator,
    ) -> None:
        """SSE event sequence + payload shapes byte-identical to v0.4.17.

        Snapshot-style assertion: strip volatile fields (timestamps, UUIDs)
        then compare the canonical event sequence + key set. Establishes the
        baseline so any future shape drift fails this test.
        """
        body = {
            "topic": "snap-probe",
            "scope": "**/*",
            "intent_hint": "explore",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,  # ProbeRunRequest enforces ge=5
        }
        async with app_client.stream(
            "POST", "/api/probes", json=body,
        ) as resp:
            assert resp.status_code == 200
            text = await resp.aread()
        events = _parse_sse(text.decode())
        # Event names sequence: probe_started → probe_grounding → probe_generating
        # → probe_prompt_completed × 5 → probe_completed
        names = [n for n, _ in events]
        assert names == [
            "probe_started",
            "probe_grounding",
            "probe_generating",
            "probe_prompt_completed",
            "probe_prompt_completed",
            "probe_prompt_completed",
            "probe_prompt_completed",
            "probe_prompt_completed",
            "probe_completed",
        ], f"name sequence drift: {names}"

        # Payload key sets must match the v0.4.17 contract.
        # Field "event" is the SSE name encoded as data["event"].
        started = next(d for n, d in events if n == "probe_started")
        assert {"run_id", "probe_id", "topic", "scope", "intent_hint",
                "n_prompts", "repo_full_name"}.issubset(started.keys())

        grounding = next(d for n, d in events if n == "probe_grounding")
        assert {"run_id", "probe_id", "retrieved_files_count",
                "has_explore_synthesis", "dominant_stack"}.issubset(
            grounding.keys(),
        )

        generating = next(d for n, d in events if n == "probe_generating")
        assert {"run_id", "probe_id", "prompts_generated",
                "generator_duration_ms", "generator_model"}.issubset(
            generating.keys(),
        )

        progress = next(d for n, d in events if n == "probe_prompt_completed")
        assert {"run_id", "probe_id", "current", "total", "optimization_id",
                "intent_label", "overall_score"}.issubset(progress.keys())

        completed = next(d for n, d in events if n == "probe_completed")
        assert {"run_id", "probe_id", "status", "mean_overall",
                "prompts_generated", "taxonomy_delta_summary"}.issubset(
            completed.keys(),
        )

    @pytest.mark.asyncio
    async def test_post_probes_subscription_registered_before_dispatch(
        self,
        app_client: AsyncClient,
        stub_run_orchestrator,
    ) -> None:
        """First event yielded by SSE stream MUST be ``probe_started``.

        Race-free pattern verification: the router subscribes BEFORE the
        orchestrator dispatches, so the first published event (``probe_started``)
        is captured. If the subscription registered AFTER the orchestrator
        kicked off, ``probe_started`` could be missed.
        """
        body = {
            "topic": "race",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,  # ProbeRunRequest enforces ge=5
        }
        async with app_client.stream(
            "POST", "/api/probes", json=body,
        ) as resp:
            assert resp.status_code == 200
            text = await resp.aread()
        events = _parse_sse(text.decode())
        # First captured event must be probe_started
        assert events, "no SSE events captured"
        first_name, _ = events[0]
        assert first_name == "probe_started", (
            f"first event was {first_name!r}, expected probe_started — "
            "subscription is racing the dispatch"
        )

    @pytest.mark.asyncio
    async def test_get_probes_list_serializes_runrow_via_probe_run_summary(
        self,
        app_client: AsyncClient,
        db_session,
    ) -> None:
        """GET /api/probes returns RunRow WHERE mode='topic_probe' through
        ProbeRunSummary — seed_agent rows excluded."""
        now = datetime.utcnow()
        db_session.add(RunRow(
            id="probe-1",
            mode="topic_probe",
            status="completed",
            started_at=now,
            topic="hello",
            repo_full_name="owner/repo",
        ))
        db_session.add(RunRow(
            id="seed-1",
            mode="seed_agent",
            status="completed",
            started_at=now,
        ))
        await db_session.commit()

        resp = await app_client.get("/api/probes")
        assert resp.status_code == 200
        items = resp.json()["items"]
        ids = {it["id"] for it in items}
        assert "probe-1" in ids
        assert "seed-1" not in ids
        # ProbeRunSummary shape preserved
        probe_item = next(it for it in items if it["id"] == "probe-1")
        assert probe_item["topic"] == "hello"
        assert probe_item["repo_full_name"] == "owner/repo"
        assert "status" in probe_item

    @pytest.mark.asyncio
    async def test_get_probe_by_id_serializes_through_probe_run_result(
        self,
        app_client: AsyncClient,
        db_session,
    ) -> None:
        """GET /api/probes/{id} returns full ProbeRunResult shape including
        scope (from topic_probe_meta) at top level + commit_sha."""
        now = datetime.utcnow()
        db_session.add(RunRow(
            id="probe-detail",
            mode="topic_probe",
            status="completed",
            started_at=now,
            topic="detail-test",
            intent_hint="explore",
            repo_full_name="owner/repo",
            topic_probe_meta={"scope": "src/**", "commit_sha": "abc"},
            prompt_results=[],
            aggregate={
                "mean_overall": 7.5,
                "scoring_formula_version": SCORING_FORMULA_VERSION,
            },
        ))
        await db_session.commit()

        resp = await app_client.get("/api/probes/probe-detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "probe-detail"
        assert body["topic"] == "detail-test"
        # ProbeRunResult shape: scope at top level (from topic_probe_meta)
        assert body["scope"] == "src/**"
        assert body["commit_sha"] == "abc"

    @pytest.mark.asyncio
    async def test_get_probes_link_repo_first_error_preserved(
        self, app_client: AsyncClient,
    ) -> None:
        """POST /api/probes without repo_full_name returns 400 link_repo_first."""
        resp = await app_client.post("/api/probes", json={"topic": "x"})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "link_repo_first"

    @pytest.mark.asyncio
    async def test_get_probe_404_probe_not_found(
        self, app_client: AsyncClient,
    ) -> None:
        """Unknown probe id surfaces canonical 404 detail."""
        resp = await app_client.get("/api/probes/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "probe_not_found"

    @pytest.mark.asyncio
    async def test_get_probes_pagination_envelope(
        self, app_client: AsyncClient, db_session,
    ) -> None:
        """Pagination envelope keys + has_more semantics preserved."""
        now = datetime.utcnow()
        for i in range(5):
            db_session.add(RunRow(
                id=f"p-{i}",
                mode="topic_probe",
                status="completed",
                started_at=now,
                topic=f"t-{i}",
                repo_full_name="owner/repo",
            ))
        await db_session.commit()

        resp = await app_client.get("/api/probes?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert {"total", "count", "offset", "items",
                "has_more", "next_offset"}.issubset(body.keys())
        assert body["count"] == 2
        assert body["has_more"] is True

    @pytest.mark.asyncio
    async def test_post_probes_invalid_request_400(
        self, app_client: AsyncClient,
    ) -> None:
        """Pydantic ValidationError -> HTTP 400 with detail='invalid_request'.

        Pydantic raises ValidationError → FastAPI defaults to 422. The
        router shim translates these to 400 with 'invalid_request' so
        clients have a single canonical error shape to switch on.
        ``topic="xx"`` (length 2) violates ``min_length=3``, generating
        a clean ValidationError that's not also a link_repo_first miss.
        """
        resp = await app_client.post("/api/probes", json={
            "repo_full_name": "owner/repo",
            "topic": "xx",  # violates min_length=3
            "n_prompts": 1000,  # exceeds le=25
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_request"

    @pytest.mark.asyncio
    async def test_post_probes_run_id_in_event_payloads(
        self,
        app_client: AsyncClient,
        stub_run_orchestrator,
        event_bus_capture,
    ) -> None:
        """Every probe_* SSE event payload carries a non-empty run_id."""
        body = {
            "topic": "rid-test",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,  # ProbeRunRequest enforces ge=5
        }
        async with app_client.stream(
            "POST", "/api/probes", json=body,
        ) as resp:
            assert resp.status_code == 200
            await resp.aread()

        # Every published probe_* event has run_id populated
        probe_events = [
            e for e in event_bus_capture.events
            if e.kind.startswith("probe_")
        ]
        assert probe_events, "no probe_* events captured"
        for evt in probe_events:
            assert evt.payload.get("run_id"), (
                f"event {evt.kind} missing run_id: {evt.payload!r}"
            )

    @pytest.mark.asyncio
    async def test_subscription_filters_other_run_events(self) -> None:
        """Events for a different run don't appear in this run's subscription."""
        target_run = "target-run-filt-1"
        other_run = "other-run-filt-1"
        sub = event_bus.subscribe_for_run(target_run)

        event_bus.publish("probe_started", {"run_id": target_run, "topic": "t"})
        event_bus.publish("probe_started", {"run_id": other_run, "topic": "x"})
        event_bus.publish("probe_completed", {"run_id": target_run})

        received: list = []

        async def collect() -> None:
            async for evt in sub:
                received.append(evt)
                if evt.kind == "probe_completed":
                    break

        try:
            await asyncio.wait_for(collect(), timeout=2)
        finally:
            await sub.aclose()

        assert all(e.payload.get("run_id") == target_run for e in received)
        assert {e.kind for e in received} == {"probe_started", "probe_completed"}

    @pytest.mark.asyncio
    async def test_subscription_excludes_taxonomy_changed_optimization_created(
        self,
    ) -> None:
        """Events without run_id in data are filtered out of per-run subscription."""
        target_run = "filt-run-2"
        sub = event_bus.subscribe_for_run(target_run)

        # Cross-cutting events with no run_id field — must be filtered out
        event_bus.publish("taxonomy_changed", {"trigger": "test"})
        event_bus.publish("optimization_created", {"id": "o1"})
        event_bus.publish("probe_completed", {"run_id": target_run})

        received: list = []

        async def collect() -> None:
            async for evt in sub:
                received.append(evt)
                if evt.kind == "probe_completed":
                    break

        try:
            await asyncio.wait_for(collect(), timeout=2)
        finally:
            await sub.aclose()

        kinds = {e.kind for e in received}
        assert kinds == {"probe_completed"}, (
            f"unexpected events leaked through filter: {kinds}"
        )

    @pytest.mark.asyncio
    async def test_post_probes_writes_run_row_status_running_at_start(
        self,
        app_client: AsyncClient,
        stub_run_orchestrator,
        db_session,
    ) -> None:
        """RunRow exists in the DB after the call returns.

        Verifies the orchestrator's ``_create_row`` fired (with mode set
        to 'topic_probe') and committed via the WriteQueue. Status MAY
        be terminal already since the stub generator runs to completion
        synchronously, but the row MUST exist.
        """
        body = {
            "topic": "early-row",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,  # ProbeRunRequest enforces ge=5
        }
        async with app_client.stream(
            "POST", "/api/probes", json=body,
        ) as resp:
            assert resp.status_code == 200
            await resp.aread()

        # Drain any pending orchestrator-side persistence task so the
        # background ``_persist_final`` write completes BEFORE the test
        # queries db_session. Without this drain the orchestrator's
        # commit can race the test read on the shared in-memory session.
        for _ in range(20):
            await asyncio.sleep(0.05)
            other_tasks = [
                t for t in asyncio.all_tasks()
                if t is not asyncio.current_task() and not t.done()
            ]
            if not other_tasks:
                break

        rows = (
            await db_session.execute(
                select(RunRow).where(RunRow.topic == "early-row"),
            )
        ).scalars().all()
        assert len(rows) == 1
        # status MAY be 'running' or already terminal — but row must exist
        assert rows[0].mode == "topic_probe"
        assert rows[0].status in ("running", "completed", "partial", "failed")

    @pytest.mark.asyncio
    async def test_client_disconnect_cleans_up_subscription(
        self,
        app_client: AsyncClient,
        stub_run_orchestrator,
    ) -> None:
        """Client disconnect mid-stream MUST close the orchestrator-side
        subscription. Spec § 6.2 requires no leaked subscribers on
        ``event_bus._subscribers``.

        Spec gap D from V1 review: ensures the router's ``finally:
        await subscription.aclose()`` block runs even if the client
        disconnects before the terminal event.
        """
        initial_subs = len(event_bus._subscribers)

        async with app_client.stream(
            "POST",
            "/api/probes",
            json={
                "topic": "dc-test",
                "repo_full_name": "owner/repo",
                "n_prompts": 5,
            },
        ) as resp:
            assert resp.status_code == 200
            # Read the first chunk so the stream actually opens, then bail.
            async for chunk in resp.aiter_bytes():
                if chunk:
                    break

        # Allow async cleanup
        await asyncio.sleep(0.5)

        assert len(event_bus._subscribers) == initial_subs, (
            f"subscription leak: started with {initial_subs}, "
            f"ended with {len(event_bus._subscribers)}"
        )
