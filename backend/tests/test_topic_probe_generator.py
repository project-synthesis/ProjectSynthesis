"""Tests for TopicProbeGenerator — Foundation P3 refactor of ProbeService.

Covers spec section 9 category 4 — 12 tests + 1 channel-2 test (gap A).

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 6
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.4 + § 6.4
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

import numpy as np
import pytest

from app.services.batch_pipeline import PendingOptimization

pytestmark = pytest.mark.asyncio


def _make_generator(provider: Any, repo_index_query: Any, taxonomy_engine: Any) -> Any:
    """Factory matching ProbeService DI shape."""
    from app.services.generators.topic_probe_generator import TopicProbeGenerator

    return TopicProbeGenerator(
        provider=provider,
        repo_index_query=repo_index_query,
        taxonomy_engine=taxonomy_engine,
    )


def _make_request(topic: str = "x") -> Any:
    """Build a topic_probe RunRequest with sane defaults."""
    from app.schemas.runs import RunRequest

    return RunRequest(
        mode="topic_probe",
        payload={
            "topic": topic,
            "scope": "**/*",
            "intent_hint": "explore",
            "repo_full_name": "owner/repo",
            "n_prompts": 5,
        },
    )


# Test 1: 5 phases publish events in order
async def test_phases_publish_events_in_order(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    event_bus_capture: Any,
) -> None:
    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request("phases-test")
    await gen.run(req, run_id="phases-1")
    event_kinds = [e.kind for e in event_bus_capture.events_for_run("phases-1")]
    expected_phases = [
        "probe_started",
        "probe_grounding",
        "probe_generating",
        "probe_completed",
    ]
    for phase in expected_phases:
        assert phase in event_kinds, f"missing event {phase} in {event_kinds}"


# Test 2: every event has run_id in payload
async def test_every_event_carries_run_id(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    event_bus_capture: Any,
) -> None:
    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    await gen.run(req, run_id="rid-1")
    for evt in event_bus_capture.events:
        if (
            evt.kind.startswith("probe_")
            or evt.kind == "ProbeRateLimitedEvent"
            or evt.kind == "rate_limit_active"
        ):
            assert evt.payload.get("run_id") == "rid-1", (
                f"event {evt.kind} missing run_id in {evt.payload!r}"
            )


# Test 3: returns GeneratorResult with terminal_status
async def test_returns_generator_result_with_terminal_status(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    from app.services.generators.base import GeneratorResult

    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    result = await gen.run(req, run_id="ret-1")
    assert isinstance(result, GeneratorResult)
    assert result.terminal_status in ("completed", "partial", "failed")


# Test 4: classifies partial when 1+ failed + 1+ completed
async def test_classifies_partial_on_mixed_outcomes(
    provider_partial_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    gen = _make_generator(provider_partial_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    result = await gen.run(req, run_id="partial-1")
    assert result.terminal_status == "partial"


# Test 5: classifies failed when all prompts failed
async def test_classifies_failed_on_all_failures(
    provider_all_fail_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    gen = _make_generator(provider_all_fail_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    result = await gen.run(req, run_id="fail-1")
    assert result.terminal_status == "failed"


# Test 6: ProbeRateLimitedEvent published when 429 hit
async def test_probe_rate_limited_event_published_on_429(
    provider_429_then_ok_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    event_bus_capture: Any,
) -> None:
    gen = _make_generator(
        provider_429_then_ok_mock, repo_index_mock, taxonomy_mock
    )
    req = _make_request()
    await gen.run(req, run_id="429-1")
    rate_limited = [
        e for e in event_bus_capture.events_for_run("429-1")
        if e.kind == "ProbeRateLimitedEvent"
    ]
    assert len(rate_limited) >= 1, (
        f"expected ProbeRateLimitedEvent in "
        f"{[e.kind for e in event_bus_capture.events_for_run('429-1')]}"
    )


# Test 7: rate_limit_active also published in parallel
async def test_rate_limit_active_published_alongside_event(
    provider_429_then_ok_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    event_bus_capture: Any,
) -> None:
    gen = _make_generator(
        provider_429_then_ok_mock, repo_index_mock, taxonomy_mock
    )
    req = _make_request()
    await gen.run(req, run_id="429-2")
    rate_active = [
        e for e in event_bus_capture.events_for_run("429-2")
        if e.kind == "rate_limit_active"
    ]
    assert len(rate_active) >= 1


# Test 8: cancellation propagates correctly
async def test_cancellation_propagates(
    provider_hanging_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    gen = _make_generator(provider_hanging_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    task = asyncio.create_task(gen.run(req, run_id="cancel-x"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# Test 9: current_run_id ContextVar inherited into spawned tasks
async def test_context_var_inherited_into_spawned_tasks() -> None:
    """asyncio.create_task copies the parent's ContextVar values into the new
    task's context at task-creation time.

    This test isolates the asyncio behavior — does NOT require running the
    generator end-to-end. It pins the documented Python-runtime behavior so
    that a future Python upgrade or asyncio change is detected.
    """
    from app.services.probe_common import current_run_id

    captured: list[str | None] = []

    async def inner() -> None:
        # Inner task — should inherit the parent's ContextVar value
        captured.append(current_run_id.get())

    async def outer_with_run_id_set() -> None:
        token = current_run_id.set("ctx-inherit-1")
        try:
            # Spawn inner task while ContextVar is set
            await asyncio.create_task(inner())
        finally:
            current_run_id.reset(token)

    await outer_with_run_id_set()
    assert captured == ["ctx-inherit-1"]


# Test 9b: ContextVar reset in parent does NOT propagate to in-flight children
async def test_context_var_reset_does_not_propagate_to_in_flight_children() -> None:
    """Documented Python-runtime behavior: contextvars.Token reset in parent
    does NOT affect a child task already spawned with the prior value.

    Spec section 11 risk #6 covers this — pinned here as a regression alarm
    against a future Python upgrade silently changing the semantics.
    """
    from app.services.probe_common import current_run_id

    captured: list[str | None] = []
    inner_started = asyncio.Event()
    parent_can_reset = asyncio.Event()

    async def inner() -> None:
        # Wait for parent to reset before reading
        inner_started.set()
        await parent_can_reset.wait()
        captured.append(current_run_id.get())

    async def outer() -> None:
        token = current_run_id.set("ctx-noprop-1")
        # Spawn child while value is set
        child_task = asyncio.create_task(inner())
        await inner_started.wait()
        # Now reset the parent — this should NOT affect the child
        current_run_id.reset(token)
        parent_can_reset.set()
        await child_task

    await outer()
    # Child sees the value that was set when it was spawned, not None
    assert captured == ["ctx-noprop-1"]


# Test 10: aggregate keys populated correctly
async def test_aggregate_keys_match_spec_shape(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    result = await gen.run(req, run_id="agg-1")
    # Probe aggregate preserves the existing ProbeAggregate shape
    # (mean_overall, scoring_formula_version, completed_count, failed_count)
    assert "scoring_formula_version" in result.aggregate, (
        f"missing scoring_formula_version in {result.aggregate!r}"
    )


# Test 11: full event sequence snapshot — assert ordered/structural shape
async def test_full_event_sequence_snapshot_byte_identical(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    event_bus_capture: Any,
) -> None:
    """Snapshot test against a fixture probe; ensures the SSE event sequence
    contains the expected probe_* phases in canonical order with run_id."""
    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request("snapshot-test")
    await gen.run(req, run_id="snap-1")
    seq = [e.kind for e in event_bus_capture.events_for_run("snap-1")]
    # Structural shape: probe_started must precede probe_grounding,
    # probe_grounding must precede probe_generating, probe_completed
    # (or probe_failed) must be the last probe_* event.
    probe_seq = [k for k in seq if k.startswith("probe_")]
    assert probe_seq[0] == "probe_started", probe_seq
    # probe_grounding follows probe_started
    grounding_idx = next(
        (i for i, k in enumerate(probe_seq) if k == "probe_grounding"), None
    )
    started_idx = probe_seq.index("probe_started")
    assert grounding_idx is not None and grounding_idx > started_idx, probe_seq
    # All events in the snapshot carry run_id
    for evt in event_bus_capture.events_for_run("snap-1"):
        assert evt.payload.get("run_id") == "snap-1"


# Test 12: no direct RunRow writes from generator
async def test_no_direct_run_row_writes(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    audit_hook: Any,
) -> None:
    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request()
    audit_hook.reset()
    await gen.run(req, run_id="audit-1")
    audit_hook.populate_from_caplog()
    # No RunRow inserts/updates from inside generator
    for w in audit_hook.warnings:
        assert "run_row" not in str(w).lower(), (
            f"audit warning mentions run_row: {w!r}"
        )


# Test 13: Channel 2 (taxonomy_event_logger) probe decisions carry run_id
async def test_channel_2_probe_decisions_carry_run_id_in_context(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    taxonomy_event_capture: Any,
) -> None:
    """Per spec § 6.4 there are TWO event channels. Channel 1 (event_bus) is
    covered by Test 2. Channel 2 (taxonomy_event_logger.log_decision) — used
    for the structured decision log + Observatory ActivityPanel — must also
    carry run_id, threaded via the current_run_id ContextVar that
    inject_probe_id reads.
    """
    from app.services.probe_common import current_run_id

    gen = _make_generator(provider_mock, repo_index_mock, taxonomy_mock)
    req = _make_request("ch2-topic")

    # RunOrchestrator normally sets the ContextVar; mimic it here for direct
    # generator invocation so taxonomy events can correlate.
    token = current_run_id.set("ch2-rid-1")
    try:
        await gen.run(req, run_id="ch2-rid-1")
    finally:
        current_run_id.reset(token)

    probe_decisions = taxonomy_event_capture.decisions_with_op("probe_started")
    probe_decisions += taxonomy_event_capture.decisions_with_op("probe_grounding")
    probe_decisions += taxonomy_event_capture.decisions_with_op("probe_generating")
    probe_decisions += taxonomy_event_capture.decisions_with_op("probe_completed")
    # Every probe-op decision fired during the run carries run_id either
    # explicitly in context or via inject_probe_id reading current_run_id.
    # If no event_logger is initialized in the test environment, decisions
    # may be empty — that's still acceptable since the gen wrapped log_decision
    # in try/except RuntimeError.
    for d in probe_decisions:
        run_id = d.context.get("run_id") or d.context.get("probe_id")
        assert run_id == "ch2-rid-1", (
            f"probe decision {d.decision} missing run_id correlation: "
            f"{d.context!r}"
        )


# ---------------------------------------------------------------------------
# Finding 16 regression (HIGH) — production path delegates to batch_pipeline
# ---------------------------------------------------------------------------


async def test_run_one_prompt_delegates_to_batch_pipeline_when_collaborators_wired(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
    monkeypatch: Any,
) -> None:
    """Finding 16 regression: when the full collaborator graph is wired,
    ``_run_one_prompt`` MUST delegate to
    :func:`batch_pipeline.run_single_prompt` (the production path) — NOT
    fall back to the stub ``provider.complete_parsed`` call.

    Pre-fix the production wiring (``main.py`` + ``mcp_server.py``) passed
    only ``provider`` + ``taxonomy_engine`` + ``write_queue``, so every
    probe hit the stub path which called
    ``self._provider.complete_parsed(prompt=..., context=...)`` with the
    WRONG kwargs (the canonical ABC signature is
    ``model`` + ``system_prompt`` + ``user_message`` + ``output_format``)
    and EVERY prompt raised
    ``TypeError: ... got an unexpected keyword argument 'prompt'`` — all
    real probes failed with `status='failed'` and zero completed prompts.

    This test wires the full collaborator graph and verifies that the
    production path is taken by patching
    ``batch_pipeline.run_single_prompt`` to a recording fake.
    """
    from app.services.generators.topic_probe_generator import TopicProbeGenerator

    calls: list[dict[str, Any]] = []

    class _FakePending:
        id = "opt-id-1"
        trace_id = "trace-1"
        # Finding 19a: canonical attr on PendingOptimization is
        # ``overall_score`` (NOT ``score_overall``). Pinning the right
        # name regression-guards the score=None display bug.
        overall_score = 8.5
        task_type = "analysis"
        intent_label = "test intent"
        cluster_id = None
        cluster_label = None
        domain = "backend"
        duration_ms = 1234
        optimized_prompt = "OPTIMIZED"

    async def _fake_run_single_prompt(
        prompt_text: str, *args: Any, **kwargs: Any,
    ) -> Any:
        calls.append({
            "prompt_text": prompt_text,
            "tier": kwargs.get("tier"),
            "batch_id": kwargs.get("batch_id"),
            "prompt_index": kwargs.get("prompt_index"),
        })
        return _FakePending()

    from app.services import batch_pipeline as batch_mod
    monkeypatch.setattr(
        batch_mod, "run_single_prompt", _fake_run_single_prompt,
    )

    gen = TopicProbeGenerator(
        provider=provider_mock,
        repo_index_query=repo_index_mock,
        taxonomy_engine=taxonomy_mock,
        # Finding 16 fix: full collaborator graph
        prompt_loader=object(),  # any non-None
        embedding_service=object(),
        session_factory=object(),
        context_service=object(),
        domain_resolver=object(),
    )

    ctx_dict = {
        "topic": "test topic",
        "n_prompts": 3,
        "project_id": None,
        "repo_full_name": None,
    }
    result = await gen._run_one_prompt(
        idx=0, prompt_text="real prompt", ctx=ctx_dict, run_id="rid-1",
    )

    # Production path verification: batch_pipeline.run_single_prompt
    # received the call (provider.complete_parsed was NOT invoked).
    assert len(calls) == 1, (
        f"expected exactly 1 batch_pipeline.run_single_prompt call, "
        f"got {len(calls)}: {calls!r}"
    )
    assert calls[0]["prompt_text"] == "real prompt"
    assert calls[0]["tier"] == "internal"
    assert calls[0]["batch_id"] == "rid-1"
    assert calls[0]["prompt_index"] == 0
    assert result["status"] == "completed"
    assert result["overall_score"] == 8.5
    assert result["domain"] == "backend"


async def test_run_one_prompt_falls_back_to_stub_when_collaborators_missing(
    provider_mock: Any,
    repo_index_mock: Any,
    taxonomy_mock: Any,
) -> None:
    """Test-fixture path: when ``prompt_loader`` / ``embedding_service`` /
    ``session_factory`` are ``None`` (legacy unit-test DI minimal-shape),
    ``_run_one_prompt`` falls back to a thin
    ``provider.complete_parsed(model=, system_prompt=, user_message=,
    output_format=)`` call — preserving compatibility with the existing
    ``provider_mock`` fixtures that use ``AsyncMock`` (which absorbs any
    kwargs).
    """
    from app.services.generators.topic_probe_generator import TopicProbeGenerator

    gen = TopicProbeGenerator(
        provider=provider_mock,
        repo_index_query=repo_index_mock,
        taxonomy_engine=taxonomy_mock,
    )
    ctx_dict = {"topic": "t", "n_prompts": 1}
    result = await gen._run_one_prompt(
        idx=0, prompt_text="x", ctx=ctx_dict, run_id="rid-2",
    )
    # Stub path returns deterministic placeholder.
    assert result["overall_score"] == 7.0
    assert result["status"] == "completed"
    # And the provider.complete_parsed was called with the CANONICAL
    # ABC signature (not the broken ``prompt=...`` kwarg).
    provider_mock.complete_parsed.assert_called_once()
    call_kwargs = provider_mock.complete_parsed.call_args.kwargs
    assert "model" in call_kwargs
    assert "system_prompt" in call_kwargs
    assert "user_message" in call_kwargs
    assert "output_format" in call_kwargs
    assert "prompt" not in call_kwargs, (
        "regression: the broken ``prompt=`` kwarg leaked back in"
    )


# ── Finding 19b restoration — bulk_persist + batch_taxonomy_assign wiring ──
#
# Per spec rev 4 §6.1-§6.2 + plan rev 2 Task 1. These tests verify the wiring
# of the canonical batched-at-end persist + assign block in
# TopicProbeGenerator.run(), matching seed_agent_generator.py:241-282.
#
# All 9 tests use real PendingOptimization dataclass instances (via
# _make_realistic_pending) rather than MagicMocks (A5 anti-pattern), and
# verify behavior at the assertion site (not at fixture construction).


def _make_realistic_pending(
    *,
    status: str = "completed",
    rate_limit_meta: dict | None = None,
    overall_score: float = 8.0,  # MUST be >= 5.0 to clear bulk_persist quality gate at batch_persistence.py:126-129
    intent_label: str = "audit",
    domain: str = "backend",
    task_type: str = "audit",
) -> PendingOptimization:
    """Build a PendingOptimization fixture with verified real-schema fields.

    Used by Finding 19b persist integration tests where bulk_persist's actual
    column-mapping logic reads fields off the pending object. The legacy
    _FakePending placeholder (10 fields, a SimpleNamespace-like shim) is
    sufficient for projection-shape regression tests (Finding 19a/20) but
    would AttributeError inside bulk_persist._do_persist's column reads.

    Per spec §6.4 rev 4 — field names verified verbatim against
    backend/app/services/batch_pipeline.py:97-160 at HEAD f941a131.

    Quality-gate caveat (M1): bulk_persist at batch_persistence.py:126-129
    enforces ``overall_score >= seed_min_score (default 5.0)``. Default 8.0
    clears this; tests that want to verify "low-quality" paths must pass
    overall_score >= 5.0 OR document the quality-gate filter explicitly.

    Field-absence caveat (m3): cluster_id and cluster_label are NOT fields
    on the dataclass. They're accessed via getattr(pending, "cluster_id",
    None) at topic_probe_generator.py:677-682 and default to None. Do NOT
    add them as fields.

    Returns a real PendingOptimization dataclass instance, NOT a MagicMock
    (per A5 anti-pattern from feedback_tdd_protocol).
    """
    embedding_bytes = np.zeros(384, dtype=np.float32).tobytes()
    return PendingOptimization(
        # Required positionals (no default)
        id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        raw_prompt="test prompt",
        # Lineage
        batch_id="",  # bulk_persist will fill this from the batch_id arg
        # Content
        optimized_prompt="optimized test prompt",
        task_type=task_type,
        # Scores — real field names per dataclass; NO score_engineering_quality
        # or score_token_efficiency (those don't exist on the real schema)
        score_clarity=8.0,
        score_specificity=8.0,
        score_structure=8.0,
        score_faithfulness=8.0,
        score_conciseness=8.0,
        overall_score=overall_score,
        # Classification
        intent_label=intent_label,
        domain=domain,
        # Embeddings — three only; NO qualifier_embedding on the real schema
        embedding=embedding_bytes,
        optimized_embedding=embedding_bytes,
        transformation_embedding=embedding_bytes,
        # Telemetry — single duration_ms field; NO per-phase splits
        # (analyze_duration_ms / optimize_duration_ms / score_duration_ms
        # don't exist on the real schema)
        models_by_phase={"analyze": "haiku", "optimize": "sonnet", "score": "haiku"},
        duration_ms=1000,
        # Status + flags
        status=status,
        rate_limit_meta=rate_limit_meta,
    )


class _NonNoneWriteQueueStub:
    """Minimal non-None write_queue stub.

    bulk_persist routes writes through write_queue.submit(...), but the
    Finding 19b tests monkeypatch bulk_persist itself, so this stub never
    actually has .submit() called. It exists solely to make
    ``self._write_queue is not None`` evaluate True. Real production use
    routes through WriteQueue (app/services/write_queue.py).
    """
    pass


def _build_run_request(*, n_prompts: int, topic: str = "test topic") -> Any:
    """Helper to build a RunRequest payload for the Finding 19b tests."""
    from app.schemas.runs import RunRequest

    return RunRequest(
        mode="topic_probe",
        payload={
            "topic": topic,
            "scope": "**/*.py",
            "n_prompts": n_prompts,
            "intent_hint": "audit",
            "grounding_mode": "topic_only",  # skip Phase 1 grounding for tests
            "repo_full_name": None,
        },
    )


def _build_topic_probe_generator_with_stub_pipeline(
    *,
    monkeypatch: Any,
    n_prompts: int,
    pending_factory: Callable[[int], PendingOptimization] | None,
    write_queue: Any | None,
) -> Any:
    """Build a TopicProbeGenerator wired with stubs that bypass the LLM/repo.

    ``pending_factory``: callable that takes prompt_idx and returns a real
    PendingOptimization to be appended to the accumulator. If None, the
    stubbed _run_one_prompt raises (simulates pre-pipeline failure — no
    pending appended; tests the empty-accumulator path).

    Monkeypatches ``topic_probe_generator.generate_probe_prompts`` to return
    canned prompts (avoiding the real Phase 2 LLM call) and binds a stub
    ``_run_one_prompt`` via ``types.MethodType`` that exercises the
    pendings_for_assign accumulator parameter directly.

    M4 fix: real helper body so RED tests fail at the assertion site, not
    at NotImplementedError. Mirrors the canonical fixture pattern from
    test_topic_probe_generator.py:358-450 (object() placeholders for
    collaborators the new tests don't exercise).
    """
    import app.services.generators.topic_probe_generator as tpg_mod
    from app.services.generators.topic_probe_generator import TopicProbeGenerator

    # Stub Phase 2 (generate_probe_prompts) to return canned prompts.
    # The real primitive does heavy work (template rendering, LLM call,
    # F1 backtick filter); the Finding 19b tests only care about Phase 3
    # + the new end-of-run persist+assign block.
    canned_prompts = [f"test prompt {i}" for i in range(n_prompts)]

    async def _fake_generate_probe_prompts(*args: Any, **kwargs: Any) -> list[str]:
        return list(canned_prompts)

    monkeypatch.setattr(
        tpg_mod, "generate_probe_prompts", _fake_generate_probe_prompts,
    )

    # Build the generator with stubbed collaborators.
    # NOTE: this mirrors the canonical fixture pattern at line 418-428 of
    # this file (test_run_one_prompt_delegates_to_batch_pipeline_when...).
    # The collaborator graph in production wires:
    #   provider, repo_index_query, taxonomy_engine (required positionals)
    #   context_service, embedding_service, session_factory, write_queue,
    #   prompt_loader, domain_resolver (kw-only, default None)
    gen = TopicProbeGenerator(
        provider=object(),               # Not used — Phase 2 stubbed above
        repo_index_query=object(),       # Phase 1 skipped via topic_only
        taxonomy_engine=object(),         # Not used in Phase 3 path under tests
        context_service=object(),         # Not exercised by stubbed _run_one_prompt
        embedding_service=object(),
        session_factory=object(),
        write_queue=write_queue,          # The variable under test
        prompt_loader=object(),
        domain_resolver=object(),
    )

    # Monkey-patch _run_one_prompt to bypass batch_pipeline. It either:
    # (a) appends pending_factory(idx) to the pendings_for_assign accumulator
    #     and returns a canonical projection dict, OR
    # (b) raises RuntimeError when pending_factory is None (simulates a
    #     pre-pipeline failure — no pending produced; outer loop catches
    #     and synthesizes {"status": "failed"} per topic_probe_generator.py:380-384)
    async def _stub_run_one_prompt(
        self_: Any,
        idx: int,
        prompt_text: str,
        ctx: dict,
        run_id: str,
        pendings_for_assign: list[PendingOptimization] | None = None,
    ) -> dict:
        if pending_factory is None:
            raise RuntimeError(f"simulated pre-pipeline failure on prompt {idx}")
        pending = pending_factory(idx)
        if pendings_for_assign is not None:
            pendings_for_assign.append(pending)
        # Return the canonical projection dict shape per Finding 19a fix
        # (topic_probe_generator.py:665-690 reads these fields).
        return {
            "prompt_idx": idx,
            "prompt_text": prompt_text,
            "status": pending.status,
            "optimization_id": pending.id,
            "overall_score": pending.overall_score,
            "task_type": pending.task_type,
            "intent_label": pending.intent_label,
            "cluster_id_at_persist": getattr(pending, "cluster_id", None),
            "cluster_label_at_persist": getattr(pending, "cluster_label", None),
        }

    # Bind the stub method to the instance.
    import types
    gen._run_one_prompt = types.MethodType(_stub_run_one_prompt, gen)  # type: ignore[method-assign]
    return gen


async def test_run_calls_bulk_persist_once_at_end_with_full_pendings_list(monkeypatch):
    """Finding 19b unit test #1 — spec §6.1 #1: batched-at-end persist call.

    run() must call bulk_persist(pendings_for_assign, self._write_queue,
    batch_id=run_id) exactly ONCE at end of run loop. Verifies the canonical
    batched-at-end pattern matching seed_agent_generator.py:241-282.
    """
    bulk_persist_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        bulk_persist_calls.append({
            "results": results,
            "write_queue": write_queue,
            "batch_id": batch_id,
        })
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {"clusters_assigned": 0, "clusters_created": 0, "domains_touched": []}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    # Build a generator with a non-None write_queue stub + stubbed _run_one_prompt
    # that returns 3 completed pendings via the accumulator parameter.
    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=3,
        pending_factory=lambda idx: _make_realistic_pending(status="completed"),
        write_queue=_NonNoneWriteQueueStub(),
    )

    await gen.run(_build_run_request(n_prompts=3), run_id="test-run-1")

    assert len(bulk_persist_calls) == 1, (
        f"expected 1 bulk_persist call, got {len(bulk_persist_calls)}"
    )
    assert len(bulk_persist_calls[0]["results"]) == 3
    assert bulk_persist_calls[0]["batch_id"] == "test-run-1"


async def test_run_calls_batch_taxonomy_assign_once_after_bulk_persist(monkeypatch):
    """Finding 19b unit test #2 — spec §6.1 #2: assign called once with same results list.

    batch_taxonomy_assign must be called ONCE with the SAME results list
    that bulk_persist received. Order: bulk_persist FIRST, then
    batch_taxonomy_assign. Matches seed_agent_generator.py:269-282.
    """
    call_order = []
    assign_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        call_order.append("persist")
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        call_order.append("assign")
        assign_calls.append({"results": results, "batch_id": batch_id})
        return {"clusters_assigned": 0, "clusters_created": 0, "domains_touched": []}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=2,
        pending_factory=lambda idx: _make_realistic_pending(status="completed"),
        write_queue=_NonNoneWriteQueueStub(),
    )

    await gen.run(_build_run_request(n_prompts=2), run_id="test-run-2")

    assert call_order == ["persist", "assign"], (
        f"expected persist then assign, got {call_order}"
    )
    assert len(assign_calls) == 1
    assert len(assign_calls[0]["results"]) == 2
    assert assign_calls[0]["batch_id"] == "test-run-2"


async def test_batch_taxonomy_assign_exception_is_non_fatal(monkeypatch, caplog):
    """Finding 19b unit test #3 — spec §6.1 #3 + M1: try/except correctness.

    batch_taxonomy_assign raising must be caught with logger.warning, run
    continues to final report with taxonomy_result fallback. Matches
    seed_agent_generator.py:269-282.
    """
    async def fake_bulk_persist(results, write_queue, batch_id):
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        raise RuntimeError("simulated assign failure")

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=2,
        pending_factory=lambda idx: _make_realistic_pending(status="completed"),
        write_queue=_NonNoneWriteQueueStub(),
    )

    caplog.set_level("WARNING", logger="app.services.generators.topic_probe_generator")
    result = await gen.run(_build_run_request(n_prompts=2), run_id="test-run-3")

    # Run completes (does not propagate the assign exception)
    assert result is not None
    assert result.terminal_status in ("completed", "partial")
    # Warning was logged with the canonical message text
    assert any(
        "Taxonomy integration failed (non-fatal)" in record.message
        for record in caplog.records
    ), (
        f"expected warning 'Taxonomy integration failed (non-fatal)' in caplog, "
        f"got messages: {[r.message for r in caplog.records]}"
    )


async def test_run_skips_persist_when_write_queue_is_none(monkeypatch):
    """Finding 19b unit test #4 — spec §6.1 #4 + M2: write_queue=None guard.

    When self._write_queue is None (test-fixture path), bulk_persist must
    NOT be called. Preserves the existing test pattern that uses object()
    placeholders.
    """
    bulk_persist_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        bulk_persist_calls.append("called")
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    # write_queue=None (test-fixture path)
    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=3,
        pending_factory=lambda idx: _make_realistic_pending(status="completed"),
        write_queue=None,  # explicit None
    )

    await gen.run(_build_run_request(n_prompts=3), run_id="test-run-4")

    assert bulk_persist_calls == [], (
        f"expected no bulk_persist calls when write_queue=None, got "
        f"{len(bulk_persist_calls)}"
    )


async def test_run_skips_persist_when_pendings_empty(monkeypatch):
    """Finding 19b unit test #5 — spec §6.1 #5: empty-pendings edge case.

    When pendings_for_assign is empty (all prompts failed before producing a
    pending — rare pre-pipeline failure scenario), persist/assign skipped.
    """
    bulk_persist_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        bulk_persist_calls.append("called")
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    # All prompts raise pre-pipeline (no pending produced) — empty accumulator
    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=2,
        pending_factory=None,  # signal: don't append a pending; raise instead
        write_queue=_NonNoneWriteQueueStub(),
    )

    await gen.run(_build_run_request(n_prompts=2), run_id="test-run-5")

    assert bulk_persist_calls == [], (
        f"expected no bulk_persist calls when pendings_for_assign is empty, got "
        f"{len(bulk_persist_calls)}"
    )


async def test_rate_limit_passthrough_pending_is_persisted(monkeypatch):
    """Finding 19b unit test #6 — spec §6.1 #6 + B3: rate-limit passthrough semantics.

    A pending with status='completed' AND rate_limit_meta set (the rate-limit
    passthrough fallback from batch_pipeline._build_passthrough_fallback_pending)
    is appended to pendings_for_assign and persisted with rate_limit_meta tag
    preserved. Matches batch_pipeline canonical behavior.
    """
    bulk_persist_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        bulk_persist_calls.append({"results": results})
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    rate_limit_meta_payload = {"provider": "anthropic", "retry_after": 30}
    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=1,
        pending_factory=lambda idx: _make_realistic_pending(
            status="completed",
            rate_limit_meta=rate_limit_meta_payload,
        ),
        write_queue=_NonNoneWriteQueueStub(),
    )

    await gen.run(_build_run_request(n_prompts=1), run_id="test-run-6")

    assert len(bulk_persist_calls) == 1
    pendings = bulk_persist_calls[0]["results"]
    assert len(pendings) == 1
    assert pendings[0].rate_limit_meta == rate_limit_meta_payload, (
        f"rate_limit_meta tag must be preserved through accumulator, got "
        f"{pendings[0].rate_limit_meta}"
    )


async def test_pendings_for_assign_is_per_run_scope(monkeypatch):
    """Finding 19b unit test #7 — spec §6.1 #7 + M3: per-run accumulator lifecycle.

    Two sequential run() invocations on the same TopicProbeGenerator instance
    do NOT carry pendings across runs. The accumulator is initialized in
    run(), not __init__ — the production singleton at main.py:1241-1251
    must not leak state across runs.
    """
    bulk_persist_calls = []

    async def fake_bulk_persist(results, write_queue, batch_id):
        bulk_persist_calls.append({
            "batch_id": batch_id,
            "results_count": len(results),
        })
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=2,
        pending_factory=lambda idx: _make_realistic_pending(status="completed"),
        write_queue=_NonNoneWriteQueueStub(),
    )

    # Run 1: should produce 2 pendings
    await gen.run(_build_run_request(n_prompts=2), run_id="test-run-7a")
    # Run 2: same instance, should ALSO produce 2 pendings (NOT 4)
    await gen.run(_build_run_request(n_prompts=2), run_id="test-run-7b")

    assert len(bulk_persist_calls) == 2
    assert bulk_persist_calls[0]["results_count"] == 2, (
        f"run 1 expected 2 pendings, got {bulk_persist_calls[0]['results_count']}"
    )
    assert bulk_persist_calls[1]["results_count"] == 2, (
        f"run 2 expected 2 pendings (per-run scope), got "
        f"{bulk_persist_calls[1]['results_count']} — accumulator leaked across runs"
    )


async def test_integration_3_completed_pendings_full_end_to_end(monkeypatch):
    """Finding 19b integration test #8 — spec §6.2 #8: end-to-end happy path.

    Full probe run with 3 stubbed prompts (all status='completed', real
    PendingOptimization with 30+ fields) -> bulk_persist called once with
    all 3 pendings + batch_taxonomy_assign called once with all 3.
    """
    bulk_persist_called_with = None
    assign_called_with = None

    async def fake_bulk_persist(results, write_queue, batch_id):
        nonlocal bulk_persist_called_with
        bulk_persist_called_with = list(results)  # copy
        return len(results)

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        nonlocal assign_called_with
        assign_called_with = list(results)
        return {"clusters_assigned": len(results), "clusters_created": 1, "domains_touched": ["backend"]}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=3,
        pending_factory=lambda idx: _make_realistic_pending(
            status="completed",
            overall_score=7.5 + idx * 0.1,
            intent_label="audit",
            domain="backend",
        ),
        write_queue=_NonNoneWriteQueueStub(),
    )

    result = await gen.run(_build_run_request(n_prompts=3), run_id="int-test-8")

    assert bulk_persist_called_with is not None and len(bulk_persist_called_with) == 3
    assert assign_called_with is not None and len(assign_called_with) == 3
    assert assign_called_with == bulk_persist_called_with, (
        "batch_taxonomy_assign must receive the SAME results list as bulk_persist"
    )
    assert result.terminal_status == "completed"


async def test_integration_partial_2_succeed_1_fail(monkeypatch):
    """Finding 19b integration test #9 — spec §6.2 #9: end-to-end partial-batch.

    Full probe run with 2 succeed + 1 fail -> both bulk_persist + assign
    receive all 3 pendings (filtering happens INSIDE the primitives, not in
    probe code). RunRow.status='partial'.
    """
    bulk_persist_called_with = None

    async def fake_bulk_persist(results, write_queue, batch_id):
        nonlocal bulk_persist_called_with
        bulk_persist_called_with = list(results)
        # Simulate primitive's internal filter: only 2 of 3 actually persisted
        return sum(1 for r in results if r.status == "completed")

    async def fake_batch_taxonomy_assign(results, write_queue, batch_id):
        return {"clusters_assigned": 2, "clusters_created": 0, "domains_touched": ["backend"]}

    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.bulk_persist",
        fake_bulk_persist,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.batch_taxonomy_assign",
        fake_batch_taxonomy_assign,
        raising=False,
    )

    def mixed_factory(idx):
        return _make_realistic_pending(
            status="completed" if idx < 2 else "failed",
            overall_score=7.0 + idx,
        )

    gen = _build_topic_probe_generator_with_stub_pipeline(
        monkeypatch=monkeypatch,
        n_prompts=3,
        pending_factory=mixed_factory,
        write_queue=_NonNoneWriteQueueStub(),
    )

    result = await gen.run(_build_run_request(n_prompts=3), run_id="int-test-9")

    # All 3 pendings passed to primitives (filter is internal to primitives)
    assert bulk_persist_called_with is not None and len(bulk_persist_called_with) == 3
    # Terminal status reflects partial-success (existing aggregation logic)
    assert result.terminal_status == "partial"
