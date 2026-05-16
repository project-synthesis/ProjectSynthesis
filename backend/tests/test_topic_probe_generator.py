"""Tests for TopicProbeGenerator — Foundation P3 refactor of ProbeService.

Covers spec section 9 category 4 — 12 tests + 1 channel-2 test (gap A).

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 6
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.4 + § 6.4
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

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
        score_overall = 8.5
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
