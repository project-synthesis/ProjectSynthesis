"""v0.4.14 cycle 2 — MCP tool + sampling-pipeline migration tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class TestOptimizePendingPassthroughSourceGuard:
    """Source-grep guard — passthrough pending insert no longer uses bare session factory."""

    def test_optimize_passthrough_pending_uses_get_write_queue(self):
        import app.tools.optimize as _opt_mod
        src = Path(_opt_mod.__file__).read_text()
        # Site :125 lookup: the passthrough block has `provider="mcp_passthrough"`
        # and `routing_tier="passthrough"`. Verify the surrounding code uses
        # get_write_queue + submit, not async_session_factory.
        idx = src.find('provider="mcp_passthrough"')
        assert idx > 0, "passthrough block not found — file shape changed"
        # Find the 200-char window around the passthrough block
        window = src[max(0, idx - 800):idx + 200]
        assert 'await get_write_queue().submit(' in window or '_persist_pending_passthrough' in window, (
            "tools/optimize.py passthrough pending insert must use get_write_queue().submit"
        )
        assert 'async with async_session_factory() as db:' not in window or window.count(
            'async with async_session_factory() as db:'
        ) == 0, (
            "tools/optimize.py passthrough block still has bare session factory"
        )


class TestOptimizePendingPassthroughBehavior:
    """Behavior test — drive handler, assert submit was called with expected label."""

    async def test_optimize_pending_passthrough_routes_through_queue(
        self, monkeypatch, writer_engine_inmem,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from app.services.context_enrichment import EnrichedContext
        from app.services.heuristic_analyzer import HeuristicAnalysis
        from app.services.routing import RoutingDecision
        from app.services.write_queue import WriteQueue

        wq = WriteQueue(writer_engine_inmem)
        await wq.start()
        try:
            from app.tools import _shared
            _shared.set_write_queue(wq)
            submit_calls: list[str] = []
            orig_submit = wq.submit

            async def spy_submit(work, *, timeout=None, operation_label=None):
                submit_calls.append(operation_label or "")
                return await orig_submit(work, timeout=timeout, operation_label=operation_label)

            monkeypatch.setattr(wq, "submit", spy_submit)

            # Force passthrough tier via mocked routing manager
            decision = RoutingDecision(
                tier="passthrough", provider=None, provider_name=None,
                reason="test → passthrough",
            )
            mock_routing = MagicMock()
            mock_routing.resolve.return_value = decision
            mock_routing.state = MagicMock(provider=None)
            monkeypatch.setattr("app.tools._shared._routing", mock_routing)

            # Mock context enrichment to skip the real DB-backed pipeline
            analysis = HeuristicAnalysis(
                task_type="general", domain="general", intent_label="general",
            )
            enrichment = EnrichedContext(raw_prompt="mock", analysis=analysis)
            mock_ctx_svc = AsyncMock()
            mock_ctx_svc.enrich.return_value = enrichment
            monkeypatch.setattr("app.tools._shared._context_service", mock_ctx_svc)

            # Mock the read-side session factory used for enrichment + few-shot
            # so we don't open a real DB. The passthrough write goes through wq.
            mock_session = AsyncMock()
            mock_factory = MagicMock()
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
            monkeypatch.setattr("app.tools.optimize.async_session_factory", mock_factory)

            from app.tools.optimize import handle_optimize
            try:
                await handle_optimize(
                    prompt="This is a prompt that is long enough to pass validation.",
                    strategy=None,
                    repo_full_name=None,
                    workspace_path=None,
                    applied_pattern_ids=None,
                    ctx=None,
                )
            except Exception:
                # Downstream IO/persist failures are OK — we just need the
                # persist path to fire submit() with the right label.
                pass
            assert any(
                "optimize_passthrough_pending_insert" in c for c in submit_calls
            ), (
                "tools/optimize.py:125 passthrough pending insert must use "
                f"operation_label='optimize_passthrough_pending_insert' (saw: {submit_calls})"
            )
        finally:
            _shared.set_write_queue(None)
            await wq.stop(drain_timeout=2.0)
