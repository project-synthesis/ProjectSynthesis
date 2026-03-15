import json
import pytest
from app.schemas.pipeline_contracts import (
    AnalysisResult, DimensionScores, OptimizationResult, ScoreResult,
)


class TestHealthRouter:
    async def test_health_check(self, app_client):
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data
        assert "provider" in data

    async def test_health_no_provider(self, app_client):
        app_client._transport.app.state.provider = None
        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["provider"] is None


class TestOptimizeRouter:
    async def test_optimize_sse_stream(self, app_client, mock_provider):
        mock_provider.complete_parsed.side_effect = [
            AnalysisResult(
                task_type="coding", weaknesses=["vague"], strengths=["concise"],
                selected_strategy="chain-of-thought", strategy_rationale="good",
                confidence=0.9,
            ),
            OptimizationResult(
                optimized_prompt="Better prompt",
                changes_summary="Added specificity",
                strategy_used="chain-of-thought",
            ),
            ScoreResult(
                prompt_a_scores=DimensionScores(
                    clarity=4.0, specificity=3.0, structure=5.0,
                    faithfulness=5.0, conciseness=6.0,
                ),
                prompt_b_scores=DimensionScores(
                    clarity=8.0, specificity=8.0, structure=7.0,
                    faithfulness=9.0, conciseness=7.0,
                ),
            ),
        ]
        resp = await app_client.post(
            "/api/optimize", json={"prompt": "Write a function that sorts a list"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        assert len(events) >= 2

    async def test_optimize_missing_prompt(self, app_client):
        resp = await app_client.post("/api/optimize", json={})
        assert resp.status_code == 422

    async def test_optimize_empty_prompt(self, app_client):
        resp = await app_client.post("/api/optimize", json={"prompt": ""})
        assert resp.status_code == 422

    async def test_get_optimization_by_trace_id(self, app_client, mock_provider, db_session):
        from app.models import Optimization
        opt = Optimization(
            id="test-opt-1", raw_prompt="test", optimized_prompt="better test",
            task_type="coding", strategy_used="chain-of-thought",
            overall_score=7.5, status="completed",
            trace_id="trace-reconnect-1", provider="mock",
        )
        db_session.add(opt)
        await db_session.commit()
        resp = await app_client.get("/api/optimize/trace-reconnect-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimized_prompt"] == "better test"
        assert data["trace_id"] == "trace-reconnect-1"

    async def test_get_optimization_not_found(self, app_client):
        resp = await app_client.get("/api/optimize/nonexistent-trace")
        assert resp.status_code == 404

    async def test_no_provider_returns_503(self, app_client):
        app_client._transport.app.state.provider = None
        resp = await app_client.post("/api/optimize", json={"prompt": "test prompt"})
        assert resp.status_code == 503
