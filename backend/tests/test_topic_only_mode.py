"""RED-phase tests for v0.4.22 T2 Cycle 7 — Topic-only mode (9 tests).

Spec:  ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
       §2 (file changes — schemas/probes.py + services/probe_generation.py +
       services/generators/topic_probe_generator.py + prompts/manifest.json),
       §5 topic-only generator branch + ProbeContext schema extensions +
       probe_generation signature extensions,
       §10 Cycle 7 (9-test surface contract).
Plan:  ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 7 Task 7.1.

Surface contract pinned by these tests (RED → GREEN deltas):

1. ``ProbeContext`` (``schemas/probes.py``) gains 2 new optional fields and
   relaxes one existing required field — all backward-compat for codebase mode:

     - NEW ``topic_only: bool = False``
     - NEW ``commit_sha: str | None = None``
     - RELAX ``repo_full_name: str`` → ``str | None`` (default ``None``)

   The existing ``model_config = {"extra": "forbid"}`` is preserved so any
   typo at the call site fails Pydantic validation immediately.

2. ``generate_probe_prompts()`` (``services/probe_generation.py``) gains two
   new keyword-only kwargs with backward-compat defaults:

     - NEW ``mode: Literal['codebase', 'topic_only'] = 'codebase'``
     - NEW ``template_name: str = 'probe-agent.md'``

   ``mode='topic_only'`` flips the F1 backtick-density predicate: prompts
   WITH backtick code identifiers are dropped (the inverse of codebase
   mode). The batch ``_DROP_THRESHOLD=0.5`` is preserved at the BATCH level
   so the contract envelope (>50% dropped = ProbeGenerationError) is
   identical across both modes.

3. ``TopicProbeGenerator.run()`` (``services/generators/topic_probe_generator.py``)
   reads ``grounding_mode`` out of ``request.payload`` (default ``"codebase"``).
   On ``"topic_only"`` Phase 1 is skipped entirely — no ``probe_grounding``
   event is published, no ``RepoIndexQuery.query_curated_context`` is called,
   ProbeContext is built with ``topic_only=True`` + ``relevant_files=[]`` +
   ``explore_synthesis_excerpt=None`` + ``repo_full_name=None``. The
   ``link_repo_first`` precondition is bypassed for topic-only requests.
   The intent_hint Literal coercion uses ``"explore"`` as the safe default
   when payload's ``intent_hint`` is ``None`` (Pydantic would otherwise
   reject ``None`` against the Literal).

4. ``POST /api/probes`` (``routers/probes.py``) accepts a new ``grounding_mode``
   body field on ``ProbeRunRequest`` (validated by Pydantic; defaults to
   ``"codebase"``). The router's ``link_repo_first`` 400 guard is gated on
   ``grounding_mode == "codebase"`` so topic-only requests without a linked
   repo are accepted.

5. ``RunOrchestrator._extract_probe_meta()`` (already shipped at Cycle 6)
   threads ``grounding_mode`` into ``RunRow.topic_probe_meta`` — Cycle 7
   pins the **end-to-end** contract that the persistence path round-trips
   the field so the regression-alarm + replay flows can branch on it.

6. NEW prompt template ``prompts/probe-agent-topic-only.md`` (with
   ``manifest.json`` entry) instructs the LLM to generate prompts WITHOUT
   code references — the inverse-F1 filter then rejects any prompts that
   accidentally cite identifiers.

These tests MUST fail at the time of commit — no production code from
Cycle 7 GREEN is in place yet. Failure modes are documented inline in each
test's docstring so the GREEN-phase implementer can verify the diff.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.models import RunRow
from app.providers.base import LLMProvider
from app.schemas.probes import ProbeContext
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult
from app.services.generators.topic_probe_generator import TopicProbeGenerator
from app.services.probe_generation import (
    ProbeGenerationError,
    generate_probe_prompts,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limit() -> Any:
    """Mirror the rate-limit reset pattern from ``test_probe_router.py``.

    Probe-rate-limit storage is process-global; without reset, accumulated
    state from earlier router tests would 429 these tests' first POST.
    """
    from app.dependencies.rate_limit import reset_rate_limit_storage

    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


def _make_codebase_ctx(**overrides) -> ProbeContext:
    """Build a codebase-mode ``ProbeContext`` (current Cycle 6 contract).

    Used by Tests 4-6 to call ``generate_probe_prompts()`` under both
    codebase and topic-only modes — the topic-only mode only changes the
    filter direction, not the context shape, so a codebase-mode context
    is a valid input for both branches under test.
    """
    base = dict(
        topic="async cancellation patterns",
        scope="**/*",
        intent_hint="explore",
        repo_full_name="owner/repo",
        project_id=None,
        project_name="repo",
        dominant_stack=["python"],
        relevant_files=["backend/app/main.py"],
        explore_synthesis_excerpt="(synthesis snippet)",
        known_domains=["backend"],
        existing_clusters_brief=[{"label": "Cancellation Audits"}],
    )
    base.update(overrides)
    return ProbeContext(**base)


def _make_generator(
    *,
    provider: Any | None = None,
    repo_index_query: Any | None = None,
    taxonomy_engine: Any | None = None,
) -> TopicProbeGenerator:
    """Factory matching the ``TopicProbeGenerator.__init__`` shape from
    ``services/generators/topic_probe_generator.py:71-91`` so RED tests can
    inject minimal collaborators per spec §10 Cycle 7 A2 (don't re-implement
    Phase 2 — call existing ``generate_probe_prompts`` with new kwargs)."""
    return TopicProbeGenerator(
        provider=provider or AsyncMock(spec=LLMProvider),
        repo_index_query=repo_index_query or MagicMock(),
        taxonomy_engine=taxonomy_engine or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Stub generator for the router-driven tests (mirrors test_probe_router.py
# ``_StubProbeGenerator`` shape but records the ``request.payload`` so the
# assertions can pin which ``grounding_mode`` value the router threaded).
# ---------------------------------------------------------------------------


class _StubGroundingGenerator:
    """Records every ``request`` passed to ``run()`` and returns a
    deterministic terminal-completed ``GeneratorResult``.

    Publishes the canonical 5-phase event sequence to ``event_bus`` so the
    SSE-stream + ``event_bus_capture`` assertions in Tests 1, 2, 3 can
    differentiate codebase mode (``probe_grounding`` emitted) from
    topic-only mode (``probe_grounding`` skipped). This stub mirrors the
    contract pinned by ``test_probe_router.py:_StubProbeGenerator`` so
    behavioural drift between RED + production is caught immediately.
    """

    def __init__(
        self,
        emit_grounding: bool = True,
        n_prompts: int = 5,
    ) -> None:
        # Whether to emit a ``probe_grounding`` event — production
        # ``TopicProbeGenerator`` emits when ``grounding_mode == 'codebase'``
        # and skips when ``grounding_mode == 'topic_only'``. The stub
        # defaults to ``True`` for codebase parity; tests that want to
        # exercise the topic-only branch override via constructor.
        self.emit_grounding = emit_grounding
        self.n_prompts = n_prompts
        self.calls: list[tuple[RunRequest, str]] = []

    async def run(
        self, request: RunRequest, *, run_id: str,
    ) -> GeneratorResult:
        self.calls.append((request, run_id))
        from app.services.event_bus import event_bus

        topic = str(request.payload.get("topic", "stub-topic"))
        scope = str(request.payload.get("scope") or "**/*")
        intent_hint = str(request.payload.get("intent_hint") or "explore")
        repo = str(request.payload.get("repo_full_name") or "")
        n_prompts = int(request.payload.get("n_prompts") or self.n_prompts)

        event_bus.publish("probe_started", {
            "run_id": run_id,
            "probe_id": run_id,
            "topic": topic,
            "scope": scope,
            "intent_hint": intent_hint,
            "n_prompts": n_prompts,
            "repo_full_name": repo,
        })

        if self.emit_grounding:
            # Codebase mode — Phase 1 fires.
            event_bus.publish("probe_grounding", {
                "run_id": run_id,
                "probe_id": run_id,
                "retrieved_files_count": 1,
                "has_explore_synthesis": True,
                "dominant_stack": ["python"],
            })

        event_bus.publish("probe_generating", {
            "run_id": run_id,
            "probe_id": run_id,
            "prompts_generated": n_prompts,
            "generator_duration_ms": 12,
            "generator_model": "claude-sonnet-4-6",
        })

        for i in range(n_prompts):
            event_bus.publish("probe_prompt_completed", {
                "run_id": run_id,
                "probe_id": run_id,
                "current": i + 1,
                "total": n_prompts,
                "optimization_id": "",
                "intent_label": "topic-only",
                "overall_score": 7.0,
                "status": "completed",
            })

        event_bus.publish("probe_completed", {
            "run_id": run_id,
            "probe_id": run_id,
            "status": "completed",
            "mean_overall": 7.0,
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
            terminal_status="completed",  # type: ignore[arg-type]
            prompts_generated=n_prompts,
            prompt_results=[],
            aggregate={"mean_overall": 7.0, "scoring_formula_version": 4},
            taxonomy_delta={
                "domains_created": [],
                "sub_domains_created": [],
                "clusters_created": [],
                "clusters_split": [],
                "proposal_rejected_min_source_clusters": 0,
            },
            final_report="# stub report",
        )


@pytest_asyncio.fixture
async def stub_grounding_orchestrator(app_client: AsyncClient):
    """Install a ``RunOrchestrator`` whose ``topic_probe`` generator is the
    grounding-aware stub above.

    Mirrors ``test_probe_router.py::stub_run_orchestrator`` — the orchestrator
    runs the real ``WriteQueue.submit`` flow (so ``RunRow.topic_probe_meta``
    persists with the canonical 3-key dict) but the generator itself is a
    deterministic stub. Topic-only branch behaviour is verified by inspecting
    ``gen.calls`` after the request returns.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubGroundingGenerator(emit_grounding=False)  # topic-only default
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={
            # All 3 modes route to the same stub so the orchestrator's
            # dispatch resolves regardless of how production wires
            # ``replay_run`` etc.
            "topic_probe": stub_gen,
            "seed_agent": stub_gen,
            "replay_run": stub_gen,
        },
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator, stub_gen
    finally:
        app.state.run_orchestrator = previous


@pytest_asyncio.fixture
async def codebase_stub_orchestrator(app_client: AsyncClient):
    """Same shape as ``stub_grounding_orchestrator`` but with the stub
    configured to emit a ``probe_grounding`` event (codebase mode default).

    Used by Test 2 to assert that the default-mode router path still
    triggers Phase 1.
    """
    from app.main import app
    from app.services.run_orchestrator import RunOrchestrator

    write_queue = app.state.write_queue
    stub_gen = _StubGroundingGenerator(emit_grounding=True)
    orchestrator = RunOrchestrator(
        write_queue=write_queue,
        generators={
            "topic_probe": stub_gen,
            "seed_agent": stub_gen,
            "replay_run": stub_gen,
        },
    )
    previous = getattr(app.state, "run_orchestrator", None)
    app.state.run_orchestrator = orchestrator
    try:
        yield orchestrator, stub_gen
    finally:
        app.state.run_orchestrator = previous


# ===========================================================================
# Test 1 — topic_only skips Phase 1 (no probe_grounding event)
# ===========================================================================


@pytest.mark.asyncio
async def test_probe_request_with_grounding_mode_topic_only_skips_phase_1(
    app_client: AsyncClient,
    stub_grounding_orchestrator,
    event_bus_capture,
) -> None:
    """Spec §5 topic-only branch + §10 Cycle 7 test #1.

    ``POST /api/probes`` with ``{topic, n_prompts, grounding_mode='topic_only',
    repo_full_name=None, scope=None}`` MUST:

      1. NOT publish a ``probe_grounding`` event (Phase 1 entirely skipped).
      2. Forward ``grounding_mode='topic_only'`` into the generator's
         ``request.payload`` so the generator can branch internally.

    RED failure mode: ``ProbeRunRequest`` declares ``model_config =
    {"extra": "forbid"}`` and does NOT define ``grounding_mode``. The router
    therefore raises Pydantic ``ValidationError`` → 400 ``invalid_request``.
    Even bypassing the schema (extra='allow' or new field), the router's
    pre-stream gate at ``routers/probes.py:172-173`` rejects requests
    missing ``repo_full_name`` with 400 ``link_repo_first``. Both failures
    short-circuit BEFORE the generator runs, so the stub's ``calls`` list
    stays empty and no SSE events fire.
    """
    orchestrator, stub_gen = stub_grounding_orchestrator

    body = {
        "topic": "explore async error handling",
        "n_prompts": 5,
        "grounding_mode": "topic_only",
        # Explicitly omit repo_full_name AND scope — topic-only path
        # must accept this combination.
    }
    async with app_client.stream(
        "POST", "/api/probes", json=body,
    ) as resp:
        assert resp.status_code == 200, (
            f"topic-only request must be accepted (no link_repo_first); "
            f"got {resp.status_code}"
        )
        await resp.aread()

    # The generator was invoked and saw grounding_mode='topic_only' in payload.
    assert stub_gen.calls, (
        "stub generator must have been dispatched; an empty calls list "
        "means the router rejected the request before reaching the "
        "orchestrator (the link_repo_first or invalid_request gate is "
        "still active for topic_only mode)"
    )
    request, _ = stub_gen.calls[0]
    assert request.payload.get("grounding_mode") == "topic_only", (
        f"router must thread grounding_mode='topic_only' into payload; "
        f"got payload={request.payload!r}"
    )
    assert request.payload.get("repo_full_name") in (None, ""), (
        f"topic-only request must allow null repo_full_name; got "
        f"{request.payload.get('repo_full_name')!r}"
    )

    # NO probe_grounding event was published — Phase 1 skipped entirely.
    grounding_events = [
        e for e in event_bus_capture.events if e.kind == "probe_grounding"
    ]
    assert not grounding_events, (
        f"topic_only mode MUST skip Phase 1 — no probe_grounding event "
        f"should fire. Got: {[e.payload for e in grounding_events]!r}"
    )


# ===========================================================================
# Test 2 — default mode falls back to 'codebase' (backward-compat)
# ===========================================================================


@pytest.mark.asyncio
async def test_probe_request_without_grounding_mode_defaults_to_codebase(
    app_client: AsyncClient,
    codebase_stub_orchestrator,
    event_bus_capture,
) -> None:
    """Spec §5 topic-only branch — ``grounding_mode`` defaults to ``"codebase"``
    when omitted; existing callers continue to trigger Phase 1.

    Two-stage assertion:

      1. The default-mode POST emits a ``probe_grounding`` event
         (backward-compat — codebase mode fires Phase 1).
      2. The router threads ``grounding_mode='codebase'`` into the
         dispatched payload via ``ProbeRunRequest.model_dump()``. The
         spec adds ``grounding_mode: Literal['codebase', 'topic_only'] =
         'codebase'`` as a Pydantic field at line 286 (with default
         ``"codebase"``). When omitted from the request body, Pydantic
         fills the default; ``body.model_dump()`` then carries the
         resolved value into ``RunRequest.payload``. The generator's
         own ``.get('grounding_mode', 'codebase')`` default in
         ``TopicProbeGenerator.run()`` is defense-in-depth, not the
         contract pinned here — the router MUST resolve the default so
         downstream consumers see the canonical value, NOT ``None``.

    RED failure mode: ``ProbeRunRequest`` does not yet declare
    ``grounding_mode``, so ``body.model_dump()`` omits the key and the
    payload's ``.get('grounding_mode')`` returns ``None``. The strict
    equality assertion against ``"codebase"`` fails.
    """
    orchestrator, stub_gen = codebase_stub_orchestrator

    body = {
        "topic": "embedding cache invalidation",
        "n_prompts": 5,
        "repo_full_name": "owner/repo",
        # NO grounding_mode field — default behavior expected.
    }
    async with app_client.stream(
        "POST", "/api/probes", json=body,
    ) as resp:
        assert resp.status_code == 200
        await resp.aread()

    # (1) Default-mode path emits ``probe_grounding`` (Phase 1 fired).
    grounding_events = [
        e for e in event_bus_capture.events if e.kind == "probe_grounding"
    ]
    assert grounding_events, (
        "default mode (no grounding_mode field) MUST emit probe_grounding "
        "— Phase 1 backward-compat broken"
    )

    # (2) Router-resolved default — ``ProbeRunRequest.grounding_mode`` MUST
    # default to ``"codebase"`` and ``body.model_dump()`` MUST forward it.
    # Today the field doesn't exist, so ``payload['grounding_mode']`` is
    # absent → ``.get()`` returns None → assertion fails.
    assert stub_gen.calls, "default-mode request must dispatch the generator"
    request, _ = stub_gen.calls[0]
    grounding_in_payload = request.payload.get("grounding_mode")
    assert grounding_in_payload == "codebase", (
        f"router MUST resolve ProbeRunRequest.grounding_mode default to "
        f"'codebase' and forward it via body.model_dump(); got "
        f"payload['grounding_mode']={grounding_in_payload!r} (None means "
        f"the field is missing from ProbeRunRequest entirely)"
    )


# ===========================================================================
# Test 3 — topic_only bypasses link_repo_first precondition
# ===========================================================================


@pytest.mark.asyncio
async def test_probe_request_grounding_mode_topic_only_bypasses_link_repo_first(
    app_client: AsyncClient,
    stub_grounding_orchestrator,
) -> None:
    """Spec §5 topic-only branch + §4 error envelope.

    Topic-only mode does not need a linked repo (the whole point is that
    the agent generates prompts purely from the topic). The router's
    ``link_repo_first`` 400 guard MUST be gated on ``grounding_mode ==
    'codebase'`` so topic-only POSTs without ``repo_full_name`` succeed
    with 200 (default SSE) or 202 (``Prefer: respond-async``).

    RED failure mode: ``routers/probes.py:172-173`` unconditionally rejects
    ANY request missing ``repo_full_name``::

        if not isinstance(raw, dict) or not raw.get("repo_full_name"):
            raise HTTPException(status_code=400, detail="link_repo_first")

    The test asserts 200 (SSE-start) — NOT 400 ``link_repo_first``.
    """
    body = {
        "topic": "explore prompt-grounding semantics",
        "n_prompts": 5,
        "grounding_mode": "topic_only",
        # No repo_full_name — this is the whole point of topic-only.
    }
    resp = await app_client.post("/api/probes", json=body)
    # Spec §4 error envelope: topic-only should NOT trip the
    # ``link_repo_first`` 400 envelope. 200 (SSE-start) is the accepted
    # status — Cycle 8 will add 202 (``Prefer: respond-async``) but for
    # Cycle 7 we pin the SSE-default path.
    assert resp.status_code != 400, (
        f"topic_only mode must NOT trip link_repo_first; "
        f"got 400 with body={resp.text!r}"
    )
    assert resp.status_code == 200, (
        f"topic_only mode without repo should accept SSE-stream (200); "
        f"got {resp.status_code} with body={resp.text!r}"
    )
    # Defensive: detail must NOT be 'link_repo_first' if some other 4xx
    # surfaces (e.g. a validation slip).
    if resp.status_code == 400:
        body_json = resp.json()
        assert body_json.get("detail") != "link_repo_first"


# ===========================================================================
# Test 4 — topic-only selects probe-agent-topic-only.md template
# ===========================================================================


@pytest.mark.asyncio
async def test_topic_only_selects_probe_agent_topic_only_md_template(
    monkeypatch,
) -> None:
    """Spec §5 ``TopicProbeGenerator`` body (lines 906-909) — template is
    selected by ``grounding_mode``::

        template_name = (
            "probe-agent.md" if grounding_mode == "codebase"
            else "probe-agent-topic-only.md"
        )

    AND spec §2 ``probe_generation.py`` row — ``generate_probe_prompts()``
    gains ``template_name: str = 'probe-agent.md'`` kwarg.

    Patches ``probe_generation.generate_probe_prompts`` to record the
    ``template_name`` kwarg the generator passes. Topic-only mode MUST
    invoke with ``template_name='probe-agent-topic-only.md'``.

    RED failure mode: the generator's Phase 2 dispatch currently lives at
    ``topic_probe_generator.py:330-362`` and calls
    ``self._provider.complete_parsed(topic=..., n_prompts=..., context=...)``
    — NOT ``generate_probe_prompts()``. The Cycle 7 GREEN replaces the
    inline Phase 2 with a call to ``generate_probe_prompts()`` passing
    ``mode=grounding_mode`` + ``template_name=...``. Without that
    refactor, the patched callback is never invoked.
    """
    captured: dict[str, Any] = {}

    async def _fake_generate(*args, **kwargs):
        captured.update(kwargs)
        # Mirror the production return shape: ``list[str]`` with at least
        # one valid prompt so downstream Phase 3 doesn't bail on empty.
        return ["A prompt without code references"]

    import app.services.generators.topic_probe_generator as topic_probe_mod
    # Patch the rebound name AND the canonical name to cover both call
    # paths (top-level import vs module-attribute access).
    monkeypatch.setattr(
        "app.services.probe_generation.generate_probe_prompts",
        _fake_generate,
        raising=False,
    )
    # Defensive: the generator may bind ``generate_probe_prompts`` as a
    # module-level name at import time. Patch that rebound name too.
    if hasattr(topic_probe_mod, "generate_probe_prompts"):
        monkeypatch.setattr(
            topic_probe_mod, "generate_probe_prompts", _fake_generate,
        )

    provider = AsyncMock(spec=LLMProvider)
    repo_index = MagicMock()
    repo_index.query_curated_context = AsyncMock(return_value=MagicMock(
        relevant_files=[], explore_synthesis_excerpt=None, known_domains=[],
    ))
    gen = _make_generator(provider=provider, repo_index_query=repo_index)
    request = RunRequest(
        mode="topic_probe",
        payload={
            "topic": "topic-only template test",
            "scope": "**/*",
            "intent_hint": "explore",
            "n_prompts": 5,
            "grounding_mode": "topic_only",
        },
    )

    await gen.run(request, run_id="t4-rid")

    assert captured, (
        "generate_probe_prompts was never invoked — Phase 2 in TopicProbeGenerator "
        "does not yet route through the probe_generation primitive "
        "(spec §5 + §2 require this delegation in Cycle 7 GREEN)"
    )
    assert captured.get("template_name") == "probe-agent-topic-only.md", (
        f"topic_only mode must select template_name='probe-agent-topic-only.md'; "
        f"got template_name={captured.get('template_name')!r}"
    )


# ===========================================================================
# Test 5 — topic-only inverts F1 backtick predicate
# ===========================================================================


@pytest.mark.asyncio
async def test_topic_only_inverts_f1_backtick_filter() -> None:
    """Spec §2 ``probe_generation.py`` row + §5 ``generate_probe_prompts``
    signature: ``mode='topic_only'`` inverts the per-prompt predicate.

    In codebase mode the filter drops prompts WITHOUT backticks (low
    backtick density → not code-grounded). In topic-only mode the filter
    drops prompts WITH backticks (the prompt is supposed to be free of
    code references). The threshold ``_DROP_THRESHOLD=0.5`` (>50% drop
    → ProbeGenerationError) is preserved across both modes — see Test 6.

    Fixture: 10 LLM-returned prompts, 4 carry backticks, 6 are prose-only.
    Under codebase mode the 6 backtick-free prompts would be dropped; the
    inverse-F1 in topic-only mode drops the 4 backtick prompts, retaining
    6 prose-only prompts.

    RED failure mode: ``generate_probe_prompts()`` does NOT accept
    ``mode`` — calling it with ``mode='topic_only'`` raises ``TypeError:
    generate_probe_prompts() got an unexpected keyword argument 'mode'``.
    """
    provider = AsyncMock(spec=LLMProvider)
    # 10 prompts: 4 carry backticks (indices 0-3), 6 are prose only (4-9).
    prompts_response = [
        f"Audit `module_{i}.py`" if i < 4 else f"Discuss the broader landscape of pattern {i}"
        for i in range(10)
    ]
    provider.complete_parsed = AsyncMock(return_value=MagicMock(
        prompts=prompts_response,
    ))
    ctx = _make_codebase_ctx()

    # ``mode='topic_only'`` is a NEW kwarg per spec §2 (RED failure).
    result = await generate_probe_prompts(
        ctx,
        provider=provider,
        n_prompts=10,
        mode="topic_only",
        template_name="probe-agent-topic-only.md",
    )

    # Inverse-F1: only the 6 prose-only prompts survive. The 4 backtick
    # prompts are dropped.
    assert len(result) == 6, (
        f"topic_only mode must drop the 4 backtick prompts and retain the "
        f"6 prose prompts; got {len(result)} prompts: {result!r}"
    )
    for p in result:
        assert "`" not in p, (
            f"topic_only retained a prompt containing a backtick: {p!r}"
        )


# ===========================================================================
# Test 6 — topic-only preserves _DROP_THRESHOLD=0.5 batch envelope
# ===========================================================================


@pytest.mark.asyncio
async def test_topic_only_preserves_batch_drop_threshold_50pct() -> None:
    """Spec §2 ``probe_generation.py`` row: ``mode='topic_only'`` flips the
    per-prompt predicate direction but preserves the batch
    ``_DROP_THRESHOLD=0.5`` at the BATCH level. So if >50% of the LLM's
    output is rejected (in this direction: backticks present where they
    shouldn't be), the function raises ``ProbeGenerationError``.

    Fixture: 10 prompts, 6 carry backticks (60% drop under topic-only —
    over the 50% threshold). Expected: ``ProbeGenerationError`` raised
    with a message matching the backtick contract.

    RED failure mode: same as Test 5 — ``mode`` kwarg unsupported.
    """
    provider = AsyncMock(spec=LLMProvider)
    # 6/10 carry backticks → 60% drop under topic_only → over threshold.
    prompts_response = [
        f"Audit `module_{i}.py` for ..." if i < 6 else f"Discuss pattern {i}"
        for i in range(10)
    ]
    provider.complete_parsed = AsyncMock(return_value=MagicMock(
        prompts=prompts_response,
    ))
    ctx = _make_codebase_ctx()

    with pytest.raises(ProbeGenerationError, match=r"backtick"):
        await generate_probe_prompts(
            ctx,
            provider=provider,
            n_prompts=10,
            mode="topic_only",
            template_name="probe-agent-topic-only.md",
        )


# ===========================================================================
# Test 7 — safe intent_hint coercion: None → "explore"
# ===========================================================================


@pytest.mark.asyncio
async def test_safe_intent_hint_coercion_replaces_none_with_explore(
    monkeypatch,
) -> None:
    """Spec §5 ``TopicProbeGenerator.run()`` body (lines 884-895):

        # intent_hint is Literal["audit","refactor","explore","regression-test"]
        # with default "explore" — passing None would fail Pydantic validation.
        # Coerce via fallback to default for the topic-only path.
        valid_intents = ("audit", "refactor", "explore", "regression-test")
        safe_intent_hint = intent_hint if intent_hint in valid_intents else "explore"
        ...
        probe_context = ProbeContext(
            topic=topic,
            intent_hint=safe_intent_hint,
            ...
        )

    Without this coercion, building the ``ProbeContext`` with
    ``intent_hint=None`` would raise Pydantic ``ValidationError`` (the
    field's Literal annotation rejects None). The topic-only branch MUST
    apply this fallback so callers that omit ``intent_hint`` don't crash.

    The test patches ``ProbeContext`` to capture the resolved
    ``intent_hint`` value the generator passes; assertion is that the
    constructor sees ``"explore"`` (the spec-canonical default) when the
    request payload's ``intent_hint`` is ``None``.

    RED failure mode: ``TopicProbeGenerator.run()`` does not yet branch on
    ``grounding_mode``, so it never constructs a ``ProbeContext``
    locally — Phase 1 builds a context-dict and passes that to
    ``_phase_generating``. The patched ``ProbeContext`` constructor is
    never called, so ``captured`` stays empty and the test fails.
    """
    captured: dict[str, Any] = {}

    real_ctx = ProbeContext

    def _capturing_ctx(**kwargs):
        captured.update(kwargs)
        # Build a real context with safe defaults so downstream pieces
        # still receive a valid object (avoid hiding additional bugs).
        kwargs.setdefault("topic", "x")
        kwargs.setdefault("intent_hint", "explore")
        # When real_ctx requires repo_full_name (current schema), provide
        # an empty string so the test fails ONLY at the assertion below,
        # not at a Pydantic ValidationError unrelated to coercion.
        kwargs.setdefault("repo_full_name", "")
        return real_ctx(**kwargs)

    # Patch in the generator module so the GREEN-step ``ProbeContext(...)``
    # call inside ``run()`` routes through the capturing factory.
    monkeypatch.setattr(
        "app.services.generators.topic_probe_generator.ProbeContext",
        _capturing_ctx,
        raising=False,
    )

    provider = AsyncMock(spec=LLMProvider)
    # Stub out probe_generation so we don't need a real LLM call —
    # captured intent_hint is tested BEFORE Phase 2.
    async def _fake_gen(*args, **kwargs):
        return ["sample prompt one"]
    monkeypatch.setattr(
        "app.services.probe_generation.generate_probe_prompts",
        _fake_gen,
        raising=False,
    )
    import app.services.generators.topic_probe_generator as tpg_mod
    if hasattr(tpg_mod, "generate_probe_prompts"):
        monkeypatch.setattr(tpg_mod, "generate_probe_prompts", _fake_gen)

    gen = _make_generator(provider=provider)
    request = RunRequest(
        mode="topic_probe",
        payload={
            "topic": "intent coercion under topic_only",
            "scope": "**/*",
            "intent_hint": None,  # MUST coerce to 'explore'
            "n_prompts": 5,
            "grounding_mode": "topic_only",
        },
    )

    await gen.run(request, run_id="t7-rid")

    assert captured, (
        "ProbeContext was never constructed by the generator — the "
        "topic_only branch in TopicProbeGenerator.run() must instantiate "
        "ProbeContext locally per spec §5 (lines 892-902)"
    )
    assert captured.get("intent_hint") == "explore", (
        f"topic_only branch must coerce intent_hint=None → 'explore' "
        f"(Literal default); got intent_hint={captured.get('intent_hint')!r}"
    )


# ===========================================================================
# Test 8 — ProbeContext schema extensions (topic_only, commit_sha, repo|None)
# ===========================================================================


def test_probe_context_extensions_accept_topic_only_and_commit_sha() -> None:
    """Spec §2 ``schemas/probes.py`` row: ``ProbeContext`` gains 3 changes.

      - NEW ``topic_only: bool = False``
      - NEW ``commit_sha: str | None = None``
      - RELAX ``repo_full_name: str`` → ``str | None = None``

    All while preserving ``model_config = {"extra": "forbid"}`` so typo'd
    field names continue to fail.

    RED failure mode: today's schema declares ``repo_full_name: str``
    (required) at line 20 with NO defaults for ``commit_sha`` or
    ``topic_only``. Instantiating with these three fields trips
    ``ValidationError(extra="forbid")`` for the two new fields and
    ``ValidationError(missing="repo_full_name")`` for the relaxed one.
    """
    # Sub-check A: instantiation with all 3 new fields succeeds.
    ctx = ProbeContext(
        topic="testing the schema relaxation",
        topic_only=True,
        commit_sha=None,
        repo_full_name=None,
    )

    # Sub-check B: defaults are the spec-canonical ones.
    assert ctx.topic_only is True, (
        f"topic_only must accept True; got {ctx.topic_only!r}"
    )
    assert ctx.commit_sha is None, (
        f"commit_sha must accept None as default; got {ctx.commit_sha!r}"
    )
    assert ctx.repo_full_name is None, (
        f"repo_full_name must accept None (relaxed from str → str | None); "
        f"got {ctx.repo_full_name!r}"
    )

    # Sub-check C: default omission also works — minimal codebase-mode
    # ProbeContext continues to instantiate without the new fields.
    ctx_default = ProbeContext(topic="bare minimum", repo_full_name="owner/repo")
    assert ctx_default.topic_only is False, (
        f"topic_only default must be False; got {ctx_default.topic_only!r}"
    )
    assert ctx_default.commit_sha is None, (
        f"commit_sha default must be None; got {ctx_default.commit_sha!r}"
    )

    # Sub-check D: ``extra='forbid'`` is preserved — a typo'd field still
    # raises. This pins the spec's "preserve the model_config" invariant
    # so a future refactor that flips to ``extra='allow'`` is caught.
    with pytest.raises(ValidationError):
        ProbeContext(
            topic="typo guard",
            repo_full_name="owner/repo",
            invalid_extra_field="should fail",  # type: ignore[call-arg]
        )


# ===========================================================================
# Test 9 — grounding_mode persisted in RunRow.topic_probe_meta end-to-end
# ===========================================================================


@pytest.mark.asyncio
async def test_grounding_mode_persisted_in_run_row_topic_probe_meta(
    app_client: AsyncClient,
    stub_grounding_orchestrator,
    db_session,
) -> None:
    """Spec §10 Cycle 7 OPERATE O1 + §2 ``run_orchestrator.py`` row.

    End-to-end: a topic-only probe POST round-trips ``grounding_mode='topic_only'``
    into ``RunRow.topic_probe_meta`` (already wired at Cycle 6 in
    ``run_orchestrator.py:167-169``). Cycle 7 ADDS the surface — the router
    accepts the field and threads it into the dispatched payload, the
    orchestrator's ``_extract_probe_meta`` captures it into the row.

    Two-stage verification:

      1. Query the ``run_row`` table directly: ``topic_probe_meta``
         must contain ``"grounding_mode": "topic_only"``.
      2. ``GET /api/runs/{run_id}`` must surface the same field via the
         ``RunResult.topic_probe_meta`` JSON dict.

    RED failure mode: the router rejects the request at the
    ``link_repo_first`` gate (no repo + no grounding_mode = no path
    through) so no ``RunRow`` is ever created — the SELECT returns 0 rows
    and the GET 404s.
    """
    orchestrator, stub_gen = stub_grounding_orchestrator

    body = {
        "topic": "persistence round-trip test",
        "n_prompts": 5,
        "grounding_mode": "topic_only",
        # Note: no repo_full_name — topic-only must accept this.
    }
    async with app_client.stream(
        "POST", "/api/probes", json=body,
    ) as resp:
        assert resp.status_code == 200, (
            f"topic-only request must accept SSE-stream; got "
            f"{resp.status_code} body={await resp.aread()!r}"
        )
        await resp.aread()

    # The stub dispatch recorded the run_id used by the orchestrator —
    # use it to query the persisted row.
    assert stub_gen.calls, (
        "stub generator never dispatched — RunRow was never created; "
        "earlier gate (link_repo_first / invalid_request) blocked the path"
    )
    _, run_id = stub_gen.calls[0]
    assert run_id, "orchestrator must mint or accept a run_id"
    # Sanity — run_id is a stringified UUID per the router's mint logic.
    try:
        uuid.UUID(run_id)
    except (ValueError, TypeError):
        pytest.fail(f"run_id is not a UUID-shape string: {run_id!r}")

    # Drain orchestrator background tasks (modelled on
    # test_probe_router.py's draining pattern at lines 690-700).
    import asyncio
    for _ in range(20):
        await asyncio.sleep(0.05)
        other_tasks = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not other_tasks:
            break

    # (1) Direct SELECT from run_row.
    rows = (
        await db_session.execute(
            select(RunRow).where(RunRow.id == run_id),
        )
    ).scalars().all()
    assert len(rows) == 1, (
        f"exactly one RunRow expected for run_id={run_id}; got {len(rows)}"
    )
    row = rows[0]
    assert row.mode == "topic_probe"
    meta = row.topic_probe_meta or {}
    assert isinstance(meta, dict), (
        f"topic_probe_meta must be a dict; got {type(meta)!r}: {meta!r}"
    )
    assert meta.get("grounding_mode") == "topic_only", (
        f"RunRow.topic_probe_meta must persist grounding_mode='topic_only'; "
        f"got meta={meta!r}"
    )

    # (2) Verify via /api/runs/{run_id} so the read path also surfaces it.
    resp_get = await app_client.get(f"/api/runs/{run_id}")
    assert resp_get.status_code == 200, (
        f"GET /api/runs/{run_id} expected 200; got {resp_get.status_code} "
        f"body={resp_get.text!r}"
    )
    body_get = resp_get.json()
    meta_via_api = body_get.get("topic_probe_meta") or {}
    assert isinstance(meta_via_api, dict)
    assert meta_via_api.get("grounding_mode") == "topic_only", (
        f"GET /api/runs/{run_id}.topic_probe_meta.grounding_mode must "
        f"equal 'topic_only'; got {meta_via_api!r}"
    )


__all__ = [
    "test_probe_request_with_grounding_mode_topic_only_skips_phase_1",
    "test_probe_request_without_grounding_mode_defaults_to_codebase",
    "test_probe_request_grounding_mode_topic_only_bypasses_link_repo_first",
    "test_topic_only_selects_probe_agent_topic_only_md_template",
    "test_topic_only_inverts_f1_backtick_filter",
    "test_topic_only_preserves_batch_drop_threshold_50pct",
    "test_safe_intent_hint_coercion_replaces_none_with_explore",
    "test_probe_context_extensions_accept_topic_only_and_commit_sha",
    "test_grounding_mode_persisted_in_run_row_topic_probe_meta",
]
