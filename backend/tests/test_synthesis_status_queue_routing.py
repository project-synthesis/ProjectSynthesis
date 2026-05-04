# backend/tests/test_synthesis_status_queue_routing.py
"""v0.4.14 cycle 4 — _update_synthesis_status queue routing + recovery."""
from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.asyncio


class TestUpdateSynthesisStatusSignature:
    def test_signature_includes_write_queue_kwarg(self):
        from app.routers.github_repos import _update_synthesis_status
        sig = inspect.signature(_update_synthesis_status)
        assert "write_queue" in sig.parameters
        param = sig.parameters["write_queue"]
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert param.default is None


class TestUpdateSynthesisStatusQueueRouting:
    async def test_routes_through_queue_when_threaded(
        self, monkeypatch, writer_engine_inmem,
    ):
        from app.services.write_queue import WriteQueue
        wq = WriteQueue(writer_engine_inmem)
        await wq.start()
        try:
            calls: list[str] = []
            orig = wq.submit

            async def spy(work, *, timeout=None, operation_label=None):
                calls.append(operation_label or "")
                return await orig(work, timeout=timeout, operation_label=operation_label)

            monkeypatch.setattr(wq, "submit", spy)

            from app.routers.github_repos import _update_synthesis_status
            await _update_synthesis_status(
                "owner/repo", "main", status="running", write_queue=wq,
            )
            assert any("repo_synthesis_status_update" in c for c in calls)
        finally:
            await wq.stop(drain_timeout=2.0)


class TestUpdateSynthesisStatusLegacyFallback:
    async def test_falls_back_when_no_write_queue(self):
        from app.routers.github_repos import _update_synthesis_status
        # write_queue=None default — must NOT crash even if no row exists
        await _update_synthesis_status(
            "owner/nonexistent", "main", status="error", error="test",
        )


class TestUpdateSynthesisStatusQueueStopRecovery:
    async def test_during_queue_stop_does_not_crash(self, writer_engine_inmem):
        from app.services.write_queue import WriteQueue
        wq = WriteQueue(writer_engine_inmem)
        await wq.start()
        await wq.stop(drain_timeout=1.0)  # force stop BEFORE submit

        from app.routers.github_repos import _update_synthesis_status
        # MUST NOT raise — WriteQueueStoppedError caught + logged
        await _update_synthesis_status(
            "owner/repo", "main", status="ready", write_queue=wq,
        )


class TestRunExploreSynthesisSignature:
    def test_run_explore_synthesis_accepts_write_queue_kwarg(self):
        from app.routers.github_repos import _run_explore_synthesis
        sig = inspect.signature(_run_explore_synthesis)
        assert "write_queue" in sig.parameters, (
            "_run_explore_synthesis must accept write_queue kwarg"
        )
