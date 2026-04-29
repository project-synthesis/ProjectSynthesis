"""Tests for SSE event taxonomy + probe_id correlation (Topic Probe Tier 1).

AC-C7-1 through AC-C7-4 per docs/specs/topic-probe-2026-04-29.md sec 8 Cycle 7.

Drives the ``backend/app/services/probe_event_correlation.py`` module into
existence via TDD plus the docstring + ``inject_probe_id(context)`` wiring
in ``services/taxonomy/event_logger.py::log_decision``.

The C7 design (per spec sec 4.8 + plan sec Cycle 7) is:

1. ``probe_event_correlation`` re-exports the ``current_probe_id`` ContextVar
   declared in ``probe_service.py`` (per the C4<->C7 dependency resolution)
   and exposes ``inject_probe_id(context: dict) -> dict``.
2. ``log_decision`` calls ``inject_probe_id(context)`` BEFORE persisting,
   so any taxonomy event fired while a probe is in flight carries
   ``context.probe_id`` automatically (no per-call-site wiring).
3. The accepted ``path: str`` values gain ``"probe"`` in the docstring.

These RED-phase tests assert the POST-FIX behavior so they fail today
(import error on the missing module + missing context.probe_id correlation)
and flip green when the GREEN phase lands the helper + log_decision call.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.taxonomy.event_logger import (
    TaxonomyEventLogger,
    reset_event_logger,
    set_event_logger,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Local-process event logger (publishes to bus disabled, sync writes)."""
    return TaxonomyEventLogger(
        events_dir=tmp_path, publish_to_bus=False, cross_process=False,
    )


@pytest.fixture
def cross_process_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Cross-process logger (MCP-server-shaped — forwards via HTTP POST)."""
    return TaxonomyEventLogger(
        events_dir=tmp_path, publish_to_bus=False, cross_process=True,
    )


@pytest.fixture(autouse=True)
def _reset_logger_singleton() -> None:
    """Clear the module singleton between tests so set_event_logger races.

    Mirrors the cleanup pattern in test_event_delivery.py.
    """
    reset_event_logger()
    yield
    reset_event_logger()


# ---------------------------------------------------------------------------
# AC-C7-1: All 6 probe_* events emit via log_decision
# ---------------------------------------------------------------------------


class TestProbeEventsEmitViaLogDecision:
    """ProbeService.run() should drive the 6 documented probe_* events
    through ``get_event_logger().log_decision(path='probe', ...)`` so they
    land in the daily JSONL + ring buffer alongside hot/warm/cold events.

    The 6 events per spec sec 4.8 are:
      probe_started, probe_grounding, probe_generating,
      probe_prompt_completed (per-prompt), probe_completed, probe_failed.
    """

    @pytest.mark.asyncio
    async def test_six_probe_events_emit_via_log_decision(
        self,
        db_session,
        local_logger: TaxonomyEventLogger,
    ) -> None:
        """ProbeService.run() routes probe_* events through log_decision(path='probe').

        Counts log_decision calls with path='probe'. For an n_prompts=5 run
        the floor is 6 distinct event ops covered: started + grounding +
        generating + prompt_completed (per prompt) + completed. ``failed``
        only fires on error and is exercised separately by C7's negative
        path test below if extended in REFACTOR.
        """
        # Late import — probe_service imports event_logger module-internally,
        # so the singleton must already be set.
        set_event_logger(local_logger)
        from app.schemas.probes import ProbeRunRequest
        from app.services.probe_service import ProbeService

        # Mock collaborators (mirrors test_probe_service.py fixtures).
        provider = AsyncMock()
        async def _complete_parsed(*args, **kwargs):
            res = MagicMock()
            res.prompts = [
                "Audit `repo_index_service.invalidate` cache logic.",
                "Compare `explore_cache.py` TTL vs curated cache.",
                "Identify race conditions in `RepoIndexQuery.refresh`.",
                "Review SHA collision handling in `repo_index_service.py`.",
                "Find missing invalidation hooks for `file_rename` events.",
            ]
            res.model = "claude-haiku-4-5"
            return res
        provider.complete_parsed = AsyncMock(side_effect=_complete_parsed)
        provider.complete_parsed_streaming = AsyncMock(
            side_effect=_complete_parsed,
        )

        repo_query = MagicMock()
        curated = MagicMock()
        curated.selected_files = [
            {"path": "backend/app/services/repo_index_service.py", "score": 0.9},
        ]
        curated.context_text = "Repo index uses SHA-keyed cache."
        curated.explore_synthesis_excerpt = "Repo index uses SHA-keyed cache."
        curated.dominant_stack = ["python"]
        repo_query.query_curated_context = AsyncMock(return_value=curated)

        ctx_service = MagicMock()
        enriched = MagicMock()
        enriched.codebase_context = ""
        enriched.strategy_intelligence = ""
        enriched.applied_patterns = []
        enriched.divergence_alerts = ""
        enriched.heuristic_analysis = MagicMock(
            task_type="analysis", domain="backend",
        )
        enriched.enrichment_meta = {}
        ctx_service.enrich = AsyncMock(return_value=enriched)

        event_bus = MagicMock()
        event_bus.publish = MagicMock()

        svc = ProbeService(
            db_session, provider, repo_query, ctx_service, event_bus,
        )
        request = ProbeRunRequest(
            topic="embedding cache invalidation",
            scope=None,
            intent_hint=None,
            n_prompts=5,
            repo_full_name="owner/repo",
        )

        # Spy on log_decision so we can count path='probe' calls without
        # depending on JSONL parsing or ring-buffer eviction order.
        with patch.object(
            local_logger, "log_decision", wraps=local_logger.log_decision,
        ) as spy:
            async for _ in svc.run(request, probe_id="p-c7-1"):
                pass

        probe_calls = [
            c for c in spy.call_args_list
            if c.kwargs.get("path") == "probe"
        ]
        # Floor: started + grounding + generating + 5*prompt_completed
        # + completed = 8. Allow >= 8 so GREEN can add additional helpful
        # decision events without churning this assertion.
        assert len(probe_calls) >= 8, (
            f"Expected >=8 log_decision(path='probe') calls, "
            f"got {len(probe_calls)}: "
            f"{[c.kwargs.get('op') for c in probe_calls]}"
        )

        # Verify the documented op set is covered.
        ops = {c.kwargs.get("op") for c in probe_calls}
        assert "probe_started" in ops
        assert "probe_grounding" in ops
        assert "probe_generating" in ops
        assert "probe_prompt_completed" in ops
        assert "probe_completed" in ops


# ---------------------------------------------------------------------------
# AC-C7-2: probe_id correlation on existing taxonomy events when ContextVar set
# ---------------------------------------------------------------------------


class TestProbeIdCorrelation:
    """When ``current_probe_id`` ContextVar is set, ``log_decision`` should
    inject ``probe_id`` into ``context`` so downstream consumers can filter
    Activity-Panel events by originating probe."""

    def test_probe_id_correlation_when_var_set(
        self, local_logger: TaxonomyEventLogger,
    ) -> None:
        """ContextVar set -> taxonomy event context gains ``probe_id``."""
        # Import via the C7 module — RED-phase failure mode is ImportError
        # because probe_event_correlation.py does not yet exist.
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        # Sanity: helper is a callable that returns a dict.
        assert callable(inject_probe_id)

        token = current_probe_id.set("p-corr-set")
        try:
            local_logger.log_decision(
                path="warm",
                op="domain_created",
                decision="accepted",
                cluster_id="c-domain-1",
                context={"label": "embeddings", "member_count": 7},
            )
        finally:
            current_probe_id.reset(token)

        # Inspect ring buffer — the most-recent event should carry probe_id.
        recent = local_logger.get_recent(limit=1, op="domain_created")
        assert len(recent) == 1, (
            "domain_created event missing from ring buffer"
        )
        ctx = recent[0].get("context") or {}
        assert ctx.get("probe_id") == "p-corr-set", (
            f"probe_id missing from context: {ctx}"
        )
        # Original payload preserved alongside the injected key.
        assert ctx.get("label") == "embeddings"
        assert ctx.get("member_count") == 7

    def test_no_probe_id_when_var_unset(
        self, local_logger: TaxonomyEventLogger,
    ) -> None:
        """ContextVar unset -> taxonomy event context has NO ``probe_id``.

        Backward-compat: existing ring-buffer / JSONL consumers must
        tolerate events whose context dict lacks the new key.
        """
        # Trigger the GREEN-phase code path (so test fails at RED via
        # ImportError, not via stale assertion semantics).
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        assert callable(inject_probe_id)
        # Defensive: ensure the ContextVar default is None outside any
        # set/reset window so this test reflects production reality.
        assert current_probe_id.get() is None

        local_logger.log_decision(
            path="warm",
            op="domain_created",
            decision="accepted",
            cluster_id="c-domain-2",
            context={"label": "frontend", "member_count": 12},
        )

        recent = local_logger.get_recent(limit=1, op="domain_created")
        assert len(recent) == 1
        ctx = recent[0].get("context") or {}
        assert "probe_id" not in ctx, (
            f"probe_id leaked into context when ContextVar unset: {ctx}"
        )
        # Original payload still rendered verbatim.
        assert ctx.get("label") == "frontend"


# ---------------------------------------------------------------------------
# AC-C7-3: Cross-process forwarding via notify_event_bus carries probe_id
# ---------------------------------------------------------------------------


class TestCrossProcessForwarding:
    """When the MCP server process emits a probe event under a probe-set
    ContextVar, the forwarded payload (delivered via
    ``notify_event_bus`` HTTP POST to /api/events/_publish) must carry
    ``context.probe_id`` so the backend ring buffer is populated with
    correlation intact."""

    @pytest.mark.asyncio
    async def test_cross_process_forward_via_notify_event_bus(
        self, cross_process_logger: TaxonomyEventLogger,
    ) -> None:
        """MCP-process probe event -> notify_event_bus payload carries probe_id."""
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        assert callable(inject_probe_id)

        notify_calls: list[tuple[str, dict]] = []

        async def _capture(event_type: str, data: dict) -> None:
            notify_calls.append((event_type, data))

        token = current_probe_id.set("p-cross-1")
        try:
            with patch(
                "app.services.event_notification.notify_event_bus",
                new=_capture,
            ):
                cross_process_logger.log_decision(
                    path="probe",
                    op="probe_grounding",
                    decision="emitted",
                    context={
                        "retrieved_files_count": 3,
                        "has_explore_synthesis": True,
                        "dominant_stack": ["python"],
                    },
                )
                # Drain the asyncio task spawned inside log_decision so
                # the patched notify_event_bus has time to record the call.
                await cross_process_logger.drain_pending(timeout=5.0)
        finally:
            current_probe_id.reset(token)

        assert len(notify_calls) >= 1, (
            "notify_event_bus was never called for cross-process probe event"
        )
        # The forwarded payload is the full event dict with embedded
        # context — verify probe_id rode along through inject_probe_id.
        event_type, data = notify_calls[0]
        assert event_type == "taxonomy_activity"
        ctx = (data.get("context") or {})
        assert ctx.get("probe_id") == "p-cross-1", (
            f"Cross-process forward dropped probe_id: data={data}"
        )
        assert data.get("op") == "probe_grounding"
