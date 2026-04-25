import json

import pytest

from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
)


class TestHealthRouter:
    async def test_health_check(self, app_client):
        resp = await app_client.get("/api/health?probes=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "version" in data
        assert "provider" in data

    async def test_health_no_provider(self, app_client):
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.get("/api/health?probes=false")
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

    async def test_no_provider_returns_passthrough(self, app_client):
        app_client._transport.app.state.routing.set_provider(None)
        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": "This is a test prompt that is long enough to pass validation"},
        )
        # With routing, no provider degrades to passthrough tier instead of 503
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")


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

    async def test_history_surfaces_enrichment_summary_per_row(self, app_client, db_session):
        """B3 (2026-04-25): list view surfaces a compact enrichment activation
        summary so a silent profile demotion (e.g. ``code_aware`` → ``knowledge_work``
        when async vocab missed the ``_TECHNICAL_NOUNS`` set) is visible at-a-glance
        without an N+1 detail fetch.
        """
        from app.models import Optimization

        # Row A — full code_aware enrichment.
        opt_a = Optimization(
            id="enr-a", raw_prompt="audit asyncio.gather", optimized_prompt="ok",
            task_type="analysis", status="completed", provider="mock",
            context_sources={
                "codebase_context": True,
                "strategy_intelligence": True,
                "applied_patterns": False,
                "heuristic_analysis": True,
                "enrichment_meta": {
                    "enrichment_profile": "code_aware",
                    "technical_signals_detected": True,
                    "repo_relevance_score": 0.42,
                    "injection_stats": {"patterns_injected": 12, "injection_clusters": 1},
                    "curated_retrieval": {"files_included": 4},
                },
            },
        )
        # Row B — knowledge_work demotion (the bug the user observed).
        opt_b = Optimization(
            id="enr-b", raw_prompt="audit something abstract", optimized_prompt="ok",
            task_type="analysis", status="completed", provider="mock",
            context_sources={
                "codebase_context": False,
                "strategy_intelligence": False,
                "applied_patterns": False,
                "heuristic_analysis": True,
                "enrichment_meta": {
                    "enrichment_profile": "knowledge_work",
                    "injection_stats": {"patterns_injected": 0},
                    "curated_retrieval": {"files_included": 0},
                },
            },
        )
        # Row C — legacy row with no enrichment_meta block.
        opt_c = Optimization(
            id="enr-c", raw_prompt="legacy", optimized_prompt="ok",
            task_type="coding", status="completed", provider="mock",
        )
        db_session.add_all([opt_a, opt_b, opt_c])
        await db_session.commit()

        resp = await app_client.get("/api/history")
        assert resp.status_code == 200
        items = {it["id"]: it for it in resp.json()["items"]}

        a = items["enr-a"]["enrichment"]
        assert a is not None
        assert a["profile"] == "code_aware"
        assert a["codebase_context"] is True
        assert a["strategy_intelligence"] is True
        assert a["applied_patterns"] is False
        assert a["patterns_injected"] == 12
        assert a["curated_files"] == 4
        assert a["repo_relevance_score"] == 0.42

        b = items["enr-b"]["enrichment"]
        assert b is not None
        assert b["profile"] == "knowledge_work"
        assert b["codebase_context"] is False
        assert b["patterns_injected"] == 0
        assert b["curated_files"] == 0

        # Legacy row — no context_sources at all → enrichment is None.
        c = items["enr-c"]["enrichment"]
        assert c is None

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
        assert isinstance(data["available"], list)
        assert "routing_tiers" in data
        assert isinstance(data["routing_tiers"], list)


class TestProviderDetection:
    """Tests for dynamic provider detection and caching in providers.py."""

    @pytest.fixture(autouse=True)
    def _reset_api_key_cache(self):
        """Ensure cache state doesn't leak between tests."""
        import app.routers.providers as pmod

        original = pmod._api_key_cache
        yield
        pmod._api_key_cache = original

    async def test_cli_available_and_api_key_set(self, app_client, monkeypatch):
        """Both CLI and API key present → both in available list."""
        import app.routers.providers as pmod

        monkeypatch.setattr(pmod, "_CLAUDE_CLI_AVAILABLE", True)
        monkeypatch.setattr(pmod, "_has_api_key", lambda: True)

        resp = await app_client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "claude_cli" in data["available"]
        assert "anthropic_api" in data["available"]

    async def test_no_cli_no_api_key(self, app_client, monkeypatch):
        """Neither CLI nor API key → empty available list."""
        import app.routers.providers as pmod

        monkeypatch.setattr(pmod, "_CLAUDE_CLI_AVAILABLE", False)
        monkeypatch.setattr(pmod, "_has_api_key", lambda: False)

        resp = await app_client.get("/api/providers")
        data = resp.json()
        assert "claude_cli" not in data["available"]
        assert "anthropic_api" not in data["available"]

    async def test_cli_only(self, app_client, monkeypatch):
        """CLI on PATH but no API key → only claude_cli listed."""
        import app.routers.providers as pmod

        monkeypatch.setattr(pmod, "_CLAUDE_CLI_AVAILABLE", True)
        monkeypatch.setattr(pmod, "_has_api_key", lambda: False)

        resp = await app_client.get("/api/providers")
        data = resp.json()
        assert data["available"] == ["claude_cli"]

    async def test_api_key_only(self, app_client, monkeypatch):
        """API key configured but no CLI → only anthropic_api listed."""
        import app.routers.providers as pmod

        monkeypatch.setattr(pmod, "_CLAUDE_CLI_AVAILABLE", False)
        monkeypatch.setattr(pmod, "_has_api_key", lambda: True)

        resp = await app_client.get("/api/providers")
        data = resp.json()
        assert data["available"] == ["anthropic_api"]

    def test_has_api_key_cache_hit(self):
        """_has_api_key returns cached boolean within TTL window."""
        import time

        import app.routers.providers as pmod

        # Seed cache: key present
        pmod._api_key_cache = (time.monotonic(), True)
        assert pmod._has_api_key() is True

        # Seed cache: no key
        pmod._api_key_cache = (time.monotonic(), False)
        assert pmod._has_api_key() is False

    def test_has_api_key_cache_miss_after_ttl(self, monkeypatch):
        """_has_api_key re-reads when cache is stale."""
        import app.routers.providers as pmod

        # Set stale cache (timestamp 0 is always expired)
        pmod._api_key_cache = (0.0, False)
        monkeypatch.setattr(pmod, "_read_api_key", lambda: "sk-fresh1234")

        assert pmod._has_api_key() is True

    def test_invalidate_api_key_cache(self):
        """invalidate_api_key_cache forces re-read on next call."""
        import time

        import app.routers.providers as pmod

        # Fill cache
        pmod._api_key_cache = (time.monotonic(), True)
        assert pmod._has_api_key() is True

        # Invalidate
        pmod.invalidate_api_key_cache()
        assert pmod._api_key_cache == (0.0, False)

    def test_cache_stores_boolean_not_key(self, monkeypatch):
        """Cache must store a boolean, never the plaintext API key."""
        import app.routers.providers as pmod

        pmod._api_key_cache = (0.0, False)
        monkeypatch.setattr(pmod, "_read_api_key", lambda: "sk-secret1234")

        pmod._has_api_key()

        _ts, cached_value = pmod._api_key_cache
        assert cached_value is True  # boolean, not the key string
        assert cached_value != "sk-secret1234"

    def test_routing_tiers_field_requires_explicit_value(self):
        """ProviderInfo.routing_tiers has no default — must be provided."""
        from pydantic import ValidationError

        from app.routers.providers import ProviderInfo

        with pytest.raises(ValidationError):
            ProviderInfo(active_provider=None, available=[])

        # Should succeed when provided
        info = ProviderInfo(
            active_provider=None, available=[], routing_tiers=["passthrough"]
        )
        assert info.routing_tiers == ["passthrough"]


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
        assert "refine_rate_limit" in data
        assert "database_engine" in data
        assert data["database_engine"] == "sqlite"

    async def test_settings_exposes_model_catalog(self, app_client):
        """model_catalog lists each tier's id, label, and supported_efforts."""
        resp = await app_client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert "model_catalog" in data
        catalog = data["model_catalog"]
        assert isinstance(catalog, list)
        # Must include all three tiers
        tiers = {entry["tier"]: entry for entry in catalog}
        assert set(tiers.keys()) == {"opus", "sonnet", "haiku"}
        # Each entry has the required shape
        for entry in catalog:
            assert "tier" in entry
            assert "id" in entry
            assert "label" in entry
            assert "version" in entry
            assert "supported_efforts" in entry
            assert "supports_thinking" in entry
            assert isinstance(entry["supported_efforts"], list)
            assert isinstance(entry["supports_thinking"], bool)

    async def test_opus_tier_has_xhigh_in_catalog(self, app_client):
        """Opus tier (currently 4.7) exposes the full effort matrix including xhigh."""
        resp = await app_client.get("/api/settings")
        data = resp.json()
        opus = next(e for e in data["model_catalog"] if e["tier"] == "opus")
        assert "xhigh" in opus["supported_efforts"]
        assert "max" in opus["supported_efforts"]
        assert opus["supports_thinking"] is True
        assert opus["label"].startswith("Opus ")

    async def test_sonnet_tier_no_xhigh_in_catalog(self, app_client):
        """Sonnet tier exposes effort matrix without xhigh (Opus-4.7-only)."""
        resp = await app_client.get("/api/settings")
        data = resp.json()
        sonnet = next(e for e in data["model_catalog"] if e["tier"] == "sonnet")
        assert "xhigh" not in sonnet["supported_efforts"]
        assert sonnet["supports_thinking"] is True
        assert sonnet["label"].startswith("Sonnet ")

    async def test_haiku_tier_empty_efforts_in_catalog(self, app_client):
        """Haiku tier exposes empty effort list + thinking disabled."""
        resp = await app_client.get("/api/settings")
        data = resp.json()
        haiku = next(e for e in data["model_catalog"] if e["tier"] == "haiku")
        assert haiku["supported_efforts"] == []
        assert haiku["supports_thinking"] is False
        assert haiku["label"].startswith("Haiku ")


class TestGitHubAuth:
    async def test_github_login_returns_url(self, app_client):
        """Login endpoint generates OAuth URL — no auth required."""
        resp = await app_client.get("/api/github/auth/login")
        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert "github.com/login/oauth/authorize" in data["url"]

    async def test_github_me_unauthenticated(self, app_client):
        """me endpoint returns 401 without session cookie."""
        resp = await app_client.get("/api/github/auth/me")
        assert resp.status_code == 401

    async def test_github_logout_unauthenticated(self, app_client):
        """logout is idempotent — returns 200 even without session."""
        resp = await app_client.post("/api/github/auth/logout")
        assert resp.status_code == 200

    async def test_github_callback_bad_state(self, app_client):
        """Callback with mismatched state returns 400."""
        resp = await app_client.get(
            "/api/github/auth/callback?code=abc&state=wrong_state"
        )
        assert resp.status_code == 400

    async def test_github_me_with_token(self, app_client, db_session):
        """me endpoint returns user info when valid session+token exists."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from app.models import GitHubToken

        secret_key = "test-secret-key"
        key = hashlib.sha256(secret_key.encode()).digest()
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = fernet.encrypt(b"ghp_dummy_token")

        row = GitHubToken(
            session_id="test-session-me",
            token_encrypted=encrypted,
            github_login="testuser",
            github_user_id="12345",
            avatar_url="https://avatars.example.com/u/12345",
        )
        db_session.add(row)
        await db_session.commit()

        from app.config import settings
        original_secret = settings.SECRET_KEY
        settings.SECRET_KEY = secret_key

        try:
            app_client.cookies.set("session_id", "test-session-me")
            # Mock GitHub API validation (github_me now validates tokens live)
            from unittest.mock import AsyncMock, patch
            mock_user = {"login": "testuser", "id": 12345, "avatar_url": "https://avatars.example.com/u/12345"}
            with patch("app.routers.github_auth.GitHubClient") as mock_client_cls:
                mock_client_cls.return_value.get_user = AsyncMock(return_value=mock_user)
                resp = await app_client.get("/api/github/auth/me")
            assert resp.status_code == 200
            data = resp.json()
            assert data["login"] == "testuser"
            assert data["avatar_url"] == "https://avatars.example.com/u/12345"
            assert data["github_user_id"] == "12345"
        finally:
            app_client.cookies.clear()
            settings.SECRET_KEY = original_secret

    async def test_github_logout_deletes_token(self, app_client, db_session):
        """logout removes the token row from DB."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet
        from sqlalchemy import select

        from app.models import GitHubToken

        secret_key = "test-secret-key"
        key = hashlib.sha256(secret_key.encode()).digest()
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = fernet.encrypt(b"ghp_logout_token")

        row = GitHubToken(
            session_id="test-session-logout",
            token_encrypted=encrypted,
            github_login="logoutuser",
            github_user_id="99999",
        )
        db_session.add(row)
        await db_session.commit()

        app_client.cookies.set("session_id", "test-session-logout")
        resp = await app_client.post("/api/github/auth/logout")
        app_client.cookies.clear()
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        result = await db_session.execute(
            select(GitHubToken).where(GitHubToken.session_id == "test-session-logout")
        )
        assert result.scalar_one_or_none() is None


class TestGitHubRepos:
    async def test_list_repos_unauthenticated(self, app_client):
        """repos listing returns 401 without session cookie."""
        resp = await app_client.get("/api/github/repos")
        assert resp.status_code == 401

    async def test_linked_repo_unauthenticated(self, app_client):
        """linked endpoint returns 401 without session cookie."""
        resp = await app_client.get("/api/github/repos/linked")
        assert resp.status_code == 401

    async def test_unlink_repo_unauthenticated(self, app_client):
        """unlink returns 401 without session cookie."""
        resp = await app_client.delete("/api/github/repos/unlink")
        assert resp.status_code == 401

    async def test_link_repo_unauthenticated(self, app_client):
        """link returns 401 without session cookie."""
        resp = await app_client.post(
            "/api/github/repos/link", json={"full_name": "owner/repo"}
        )
        assert resp.status_code == 401

    async def test_get_linked_no_repo(self, app_client, db_session):
        """linked returns 404 when no repo is linked for the session."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from app.models import GitHubToken

        secret_key = "test-secret-key"
        key = hashlib.sha256(secret_key.encode()).digest()
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = fernet.encrypt(b"ghp_norepo_token")

        row = GitHubToken(
            session_id="test-session-norepo",
            token_encrypted=encrypted,
            github_login="norepouser",
            github_user_id="77777",
        )
        db_session.add(row)
        await db_session.commit()

        from app.config import settings
        original_secret = settings.SECRET_KEY
        settings.SECRET_KEY = secret_key

        try:
            app_client.cookies.set("session_id", "test-session-norepo")
            resp = await app_client.get("/api/github/repos/linked")
            assert resp.status_code == 404
        finally:
            app_client.cookies.clear()
            settings.SECRET_KEY = original_secret

    async def test_unlink_repo_idempotent(self, app_client, db_session):
        """unlink is idempotent — returns 200 even when nothing is linked."""
        import base64
        import hashlib

        from cryptography.fernet import Fernet

        from app.models import GitHubToken

        secret_key = "test-secret-key"
        key = hashlib.sha256(secret_key.encode()).digest()
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = fernet.encrypt(b"ghp_unlink_token")

        row = GitHubToken(
            session_id="test-session-unlink",
            token_encrypted=encrypted,
            github_login="unlinkuser",
            github_user_id="55555",
        )
        db_session.add(row)
        await db_session.commit()

        from app.config import settings
        original_secret = settings.SECRET_KEY
        settings.SECRET_KEY = secret_key

        try:
            app_client.cookies.set("session_id", "test-session-unlink")
            resp = await app_client.delete("/api/github/repos/unlink")
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
        finally:
            app_client.cookies.clear()
            settings.SECRET_KEY = original_secret


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

        resp = await app_client.get("/api/health?probes=false")
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

    async def test_health_includes_recent_errors(self, app_client, db_session):
        resp = await app_client.get("/api/health")
        data = resp.json()
        assert "recent_errors" in data
        assert "last_hour" in data["recent_errors"]
        assert "last_24h" in data["recent_errors"]

    async def test_health_counts_failed_optimizations(self, app_client, db_session):
        from app.models import Optimization

        db_session.add(Optimization(
            id="fail-1", raw_prompt="p", status="failed", provider="mock",
        ))
        db_session.add(Optimization(
            id="fail-2", raw_prompt="p", status="failed", provider="mock",
        ))
        db_session.add(Optimization(
            id="ok-1", raw_prompt="p", optimized_prompt="b",
            status="completed", provider="mock",
        ))
        await db_session.commit()

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["recent_errors"]["last_hour"] == 2
        assert data["recent_errors"]["last_24h"] == 2

    async def test_health_phase_durations(self, app_client, db_session):
        from app.models import Optimization

        db_session.add(Optimization(
            id="pd-1", raw_prompt="p", optimized_prompt="b",
            status="completed", provider="mock", duration_ms=5000,
            tokens_by_phase={"analyze_ms": 1000, "optimize_ms": 3000, "score_ms": 1000},
        ))
        db_session.add(Optimization(
            id="pd-2", raw_prompt="p", optimized_prompt="b",
            status="completed", provider="mock", duration_ms=7000,
            tokens_by_phase={"analyze_ms": 2000, "optimize_ms": 4000, "score_ms": 1000},
        ))
        await db_session.commit()

        resp = await app_client.get("/api/health")
        data = resp.json()
        # avg_duration_ms is now a simple int (overall average)
        assert isinstance(data["avg_duration_ms"], int)
        assert data["avg_duration_ms"] == 6000
        # phase_durations is a separate dict
        phases = data["phase_durations"]
        assert isinstance(phases, dict)
        assert phases["analyze_ms"] == 1500
        assert phases["optimize_ms"] == 3500
        assert phases["score_ms"] == 1000
        assert phases["total"] == 6000


class TestApiKeyManagement:
    async def test_get_api_key_not_configured(self, app_client):
        from app.config import settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        try:
            resp = await app_client.get("/api/provider/api-key")
            assert resp.status_code == 200
            data = resp.json()
            assert data["configured"] is False
        finally:
            settings.ANTHROPIC_API_KEY = original

    async def test_set_and_get_api_key(self, app_client):
        from app.config import DATA_DIR, settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        try:
            resp = await app_client.patch(
                "/api/provider/api-key",
                json={"api_key": "sk-ant-test-key-1234567890abcdefghijklmnop"},
            )
            assert resp.status_code == 200
            assert resp.json()["masked_key"] == "sk-...mnop"

            # Verify GET returns configured
            resp = await app_client.get("/api/provider/api-key")
            assert resp.json()["configured"] is True
            assert resp.json()["masked_key"] == "sk-...mnop"
        finally:
            settings.ANTHROPIC_API_KEY = original
            # Clean up credential file
            cred_file = DATA_DIR / ".api_credentials"
            if cred_file.exists():
                cred_file.unlink()

    async def test_delete_api_key(self, app_client):
        from app.config import DATA_DIR, settings
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        try:
            # Set first
            await app_client.patch(
                "/api/provider/api-key",
                json={"api_key": "sk-ant-test-key-1234567890abcdefghijklmnop"},
            )
            # Delete
            resp = await app_client.delete("/api/provider/api-key")
            assert resp.status_code == 200
            assert resp.json()["configured"] is False
        finally:
            settings.ANTHROPIC_API_KEY = original
            cred_file = DATA_DIR / ".api_credentials"
            if cred_file.exists():
                cred_file.unlink()

    async def test_set_invalid_key_rejected(self, app_client):
        resp = await app_client.patch(
            "/api/provider/api-key",
            json={"api_key": "not-a-valid-key"},
        )
        assert resp.status_code == 400
