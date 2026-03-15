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


class TestHistoryRouter:
    async def test_get_history(self, app_client, db_session):
        from app.models import Optimization

        for i in range(1, 4):
            opt = Optimization(
                id=f"hist-{i}", raw_prompt=f"prompt {i}",
                optimized_prompt=f"better {i}", task_type="coding",
                strategy_used="chain-of-thought", overall_score=7.0 + i * 0.1,
                status="completed", provider="mock",
            )
            db_session.add(opt)
        await db_session.commit()

        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        # Pagination envelope
        assert "total" in data
        assert "count" in data
        assert "offset" in data
        assert "has_more" in data
        assert "next_offset" in data
        assert "items" in data
        assert data["total"] == 3
        assert data["count"] == 3
        assert len(data["items"]) == 3

    async def test_get_history_filter_by_task_type(self, app_client, db_session):
        from app.models import Optimization

        db_session.add(Optimization(
            id="filter-1", raw_prompt="p1", optimized_prompt="o1",
            task_type="coding", status="completed", provider="mock",
        ))
        db_session.add(Optimization(
            id="filter-2", raw_prompt="p2", optimized_prompt="o2",
            task_type="writing", status="completed", provider="mock",
        ))
        db_session.add(Optimization(
            id="filter-3", raw_prompt="p3", optimized_prompt="o3",
            task_type="coding", status="completed", provider="mock",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/history?task_type=coding")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert all(item["task_type"] == "coding" for item in data["items"])


class TestFeedbackRouter:
    async def test_submit_feedback(self, app_client, db_session):
        from app.models import Optimization

        opt = Optimization(
            id="fb-opt-1", raw_prompt="test", optimized_prompt="better",
            task_type="coding", strategy_used="chain-of-thought",
            overall_score=7.5, status="completed", provider="mock",
        )
        db_session.add(opt)
        await db_session.commit()

        resp = await app_client.post("/api/feedback", json={
            "optimization_id": "fb-opt-1",
            "rating": "thumbs_up",
            "comment": "Great result!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimization_id"] == "fb-opt-1"
        assert data["rating"] == "thumbs_up"
        assert data["comment"] == "Great result!"
        assert "id" in data
        assert "created_at" in data

    async def test_submit_feedback_invalid_optimization(self, app_client):
        resp = await app_client.post("/api/feedback", json={
            "optimization_id": "nonexistent-id",
            "rating": "thumbs_up",
        })
        assert resp.status_code == 404

    async def test_get_feedback(self, app_client, db_session):
        from app.models import Feedback, Optimization

        opt = Optimization(
            id="fb-opt-2", raw_prompt="test", optimized_prompt="better",
            task_type="coding", strategy_used="chain-of-thought",
            overall_score=7.5, status="completed", provider="mock",
        )
        db_session.add(opt)
        await db_session.commit()

        fb = Feedback(
            id="fb-1", optimization_id="fb-opt-2",
            rating="thumbs_up", comment="nice",
        )
        db_session.add(fb)
        await db_session.commit()

        resp = await app_client.get("/api/feedback?optimization_id=fb-opt-2")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "aggregation" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["rating"] == "thumbs_up"
        assert data["aggregation"]["total"] == 1
        assert data["aggregation"]["thumbs_up"] == 1


class TestProvidersRouter:
    async def test_get_providers(self, app_client):
        resp = await app_client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_provider" in data
        assert data["active_provider"] == "mock"
        assert "available" in data


class TestSettingsRouter:
    async def test_get_settings(self, app_client):
        resp = await app_client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "max_raw_prompt_chars" in data
        assert "max_context_tokens" in data
        assert "optimize_rate_limit" in data
        assert "feedback_rate_limit" in data
        assert "embedding_model" in data
        assert "trace_retention_days" in data


class TestGitHubStubs:
    async def test_github_auth_returns_501(self, app_client):
        resp = await app_client.get("/api/github/auth/login")
        assert resp.status_code == 501

    async def test_github_repos_returns_501(self, app_client):
        resp = await app_client.get("/api/github/repos")
        assert resp.status_code == 501


class TestHealthMetrics:
    async def test_health_with_metrics(self, app_client, db_session):
        from app.models import Optimization

        for i in range(1, 4):
            opt = Optimization(
                id=f"health-{i}", raw_prompt=f"prompt {i}",
                optimized_prompt=f"better {i}", task_type="coding",
                strategy_used="chain-of-thought",
                overall_score=6.0 + i, duration_ms=1000 + i * 100,
                status="completed", provider="mock",
            )
            db_session.add(opt)
        await db_session.commit()

        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "score_health" in data
        assert "avg_duration_ms" in data
        # With 3 optimizations, score_health should be populated
        assert data["score_health"] is not None
        assert "last_n_mean" in data["score_health"]
        assert "last_n_stddev" in data["score_health"]
        assert "count" in data["score_health"]
        assert data["avg_duration_ms"] is not None
