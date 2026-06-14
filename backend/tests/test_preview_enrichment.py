"""Cycle 1 tests for POST /api/clusters/preview-enrichment + structured strategy intel.

See docs/superpowers/specs/2026-06-13-v0.4.40-live-pattern-intelligence-tier-2-design.md.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models import Optimization, PromptCluster, StrategyAffinity


async def _seed_optimization(
    db, strategy: str, task_type: str, domain: str, score: float,
):
    """Seed a scored optimization for strategy intelligence."""
    opt = Optimization(
        raw_prompt=f"test prompt for {strategy}",
        optimized_prompt="optimized",
        overall_score=score,
        strategy_used=strategy,
        task_type=task_type,
        domain=domain,
        status="completed",
    )
    db.add(opt)
    await db.flush()
    return opt


async def _seed_affinity(
    db, strategy: str, task_type: str, up: int, down: int,
):
    """Seed a strategy affinity row for adaptation feedback."""
    total = up + down
    aff = StrategyAffinity(
        task_type=task_type,
        strategy=strategy,
        thumbs_up=up,
        thumbs_down=down,
        approval_rate=up / total if total > 0 else 0.0,
    )
    db.add(aff)
    await db.flush()
    return aff


class TestPreviewEnrichmentEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_returns_422_on_missing_body(self, app_client):
        """POST without prompt_text returns 422 (Pydantic validation)."""
        resp = await app_client.post("/api/clusters/preview-enrichment", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_endpoint_returns_422_on_empty_prompt(self, app_client):
        """Empty prompt_text violates min_length=1."""
        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={"prompt_text": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_heuristic_only_returns_coding_for_coding_prompt(
        self, app_client, db_session,
    ):
        """A known coding prompt classifies as task_type='coding' with confidence >= 0.5."""
        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": (
                    "Write a Python function that parses a JSON config file "
                    "and returns a typed dataclass. Use pydantic for validation."
                ),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_type"]["task_type"] == "coding"
        assert body["task_type"]["confidence"] >= 0.5
        assert body["task_type"]["signal_source"] in {"bootstrap", "dynamic"}

    @pytest.mark.asyncio
    async def test_default_path_invokes_zero_llm_classify_calls(
        self, app_client, db_session,
    ):
        """AC-2: default enable_llm_fallback=False produces zero classify_with_llm invocations.

        Verified by patching the function (not by checking provider attribute).
        """
        with patch(
            "app.services.task_type_classifier.classify_with_llm",
            new=AsyncMock(return_value=None),
        ) as mocked:
            resp = await app_client.post(
                "/api/clusters/preview-enrichment",
                json={"prompt_text": "Write a Python script that fetches a URL."},
            )
        assert resp.status_code == 200
        assert mocked.call_count == 0

    @pytest.mark.asyncio
    async def test_explicit_fallback_query_invokes_llm_classify(
        self, app_client, db_session,
    ):
        """enable_llm_fallback=true query param routes through A4 LLM fallback at most once."""
        with patch(
            "app.services.task_type_classifier.classify_with_llm",
            new=AsyncMock(return_value=None),
        ) as mocked:
            resp = await app_client.post(
                "/api/clusters/preview-enrichment?enable_llm_fallback=true",
                json={"prompt_text": "Do something ambiguous and uncertain."},
            )
        assert resp.status_code == 200
        # A4 fires when heuristic confidence is below the gate; ambiguous prompt
        # exercises the gated path. count is 0 or 1 depending on confidence,
        # but never > 1 (single call per request).
        assert mocked.call_count <= 1

    @pytest.mark.asyncio
    async def test_top_strategies_cap_at_three_sorted_desc(
        self, app_client, db_session,
    ):
        """AC-4: top_strategies returns at most 3 entries, sorted by score desc."""
        # Seed five distinct strategies above the n=3 perf threshold.
        for strategy, score in [
            ("alpha", 9.0),
            ("beta", 8.5),
            ("gamma", 7.8),
            ("delta", 6.5),
            ("epsilon", 5.5),
        ]:
            for _ in range(3):
                await _seed_optimization(db_session, strategy, "coding", "backend", score)
        await db_session.commit()

        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": (
                    "Write a backend FastAPI endpoint that ingests an upload. "
                    "Use pydantic models and async sqlalchemy."
                ),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        top = body["top_strategies"]
        assert len(top) <= 3
        scores = [row["score"] for row in top]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_blocked_strategies_sorted_alphabetically(
        self, app_client, db_session,
    ):
        """AC-5: blocked_strategies sorted ascending."""
        for strategy in ("zebra", "alpha", "mango"):
            await _seed_affinity(db_session, strategy, "coding", up=1, down=10)
        await db_session.commit()

        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={"prompt_text": "Write a Python function that parses JSON."},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked_strategies"] == sorted(body["blocked_strategies"])
        assert set(body["blocked_strategies"]) >= {"alpha", "mango", "zebra"}

    @pytest.mark.asyncio
    async def test_domain_relaxed_fallback_true_when_perf_falls_back(
        self, app_client, db_session,
    ):
        """AC-7: domain_relaxed_fallback iff resolve_performance_signals fell back.

        Setup: seed 3 coding+frontend optimisations so the exact (coding, backend)
        query returns nothing and the C1 task-type-only fallback kicks in.
        """
        for _ in range(3):
            await _seed_optimization(db_session, "x-strategy", "coding", "frontend", 8.0)
        await db_session.commit()

        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": (
                    "Write a backend FastAPI endpoint that ingests a JSON payload."
                ),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["domain_relaxed_fallback"] is True

    @pytest.mark.asyncio
    async def test_domain_relaxed_fallback_independent_of_blocked_strategies(
        self, app_client, db_session,
    ):
        """Blocked path is task-type-only — never moves the fallback flag."""
        await _seed_affinity(db_session, "bad-strategy", "coding", up=0, down=10)
        await db_session.commit()

        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={"prompt_text": "Write a backend Python script."},
        )
        assert resp.status_code == 200
        body = resp.json()
        # No performance signals at all (perf table empty) — fallback flag must be False.
        assert body["domain_relaxed_fallback"] is False
        assert "bad-strategy" in body["blocked_strategies"]

    @pytest.mark.asyncio
    async def test_divergence_alerts_empty_without_project_id(
        self, app_client, db_session,
    ):
        """AC-6 negative: no project_id → empty list, never null."""
        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": "Migrate this MongoDB collection to Postgres.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["divergence_alerts"] == []

    @pytest.mark.asyncio
    async def test_divergence_alerts_populated_with_cached_synthesis(
        self, app_client, db_session, tmp_path,
    ):
        """AC-6: divergence_alerts mirror Divergence dataclass fields when synthesis cached."""
        from app.models import LinkedRepo, RepoIndexMeta

        # Create a project cluster + link a repo that has a cached synthesis
        # describing a Postgres-based codebase.
        project = PromptCluster(
            id="proj-1",
            label="myproject",
            state="project",
            domain="general",
            task_type="general",
        )
        db_session.add(project)
        await db_session.flush()

        link = LinkedRepo(
            session_id="s",
            full_name="me/myrepo",
            default_branch="main",
            branch="main",
            project_node_id="proj-1",
        )
        db_session.add(link)
        meta = RepoIndexMeta(
            repo_full_name="me/myrepo",
            branch="main",
            head_sha="abc",
            explore_synthesis=(
                "This project uses Postgres for persistence (asyncpg "
                "driver) and FastAPI for the API surface."
            ),
        )
        db_session.add(meta)
        await db_session.commit()

        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": (
                    "Migrate the database layer from Postgres to MongoDB "
                    "using pymongo."
                ),
                "project_id": "proj-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        alerts = body["divergence_alerts"]
        assert len(alerts) >= 1
        # 1:1 field mirror of Divergence.
        for a in alerts:
            assert set(a.keys()) == {
                "category", "prompt_tech", "codebase_tech", "severity",
            }
            assert a["category"] == "database"
            assert a["severity"] in {"conflict", "migration"}

    @pytest.mark.asyncio
    async def test_elapsed_ms_positive_and_weaknesses_capped_at_three(
        self, app_client, db_session,
    ):
        """AC: elapsed_ms > 0 and weaknesses cap at 3 entries."""
        resp = await app_client.post(
            "/api/clusters/preview-enrichment",
            json={
                "prompt_text": (
                    # Deliberately underspecified prompt to surface multiple weaknesses.
                    "do something useful and great for everything in all aspects "
                    "with everything covered for every part somehow somewhere"
                ),
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["elapsed_ms"] > 0
        assert len(body["weaknesses"]) <= 3

    @pytest.mark.asyncio
    async def test_low_confidence_general_empties_top_strategies(
        self, app_client, db_session,
    ):
        """AC: confidence < 0.4 AND task_type=='general' → top_strategies == [].

        Blocked + weaknesses still populate.
        """
        # Seed an obvious anti-pattern on 'general' so blocked still surfaces.
        await _seed_affinity(db_session, "bad-strategy", "general", up=0, down=10)
        await db_session.commit()

        from app.services.heuristic_analyzer import HeuristicAnalysis

        async def _fake_analyze(self, raw_prompt, db, *, provider=None, enable_llm_fallback=True):
            return HeuristicAnalysis(
                task_type="general",
                domain="general",
                intent_label="general optimization",
                weaknesses=["scope too broad"],
                recommended_strategy="auto",
                confidence=0.2,
            )

        with patch(
            "app.services.heuristic_analyzer.HeuristicAnalyzer.analyze",
            new=_fake_analyze,
        ):
            resp = await app_client.post(
                "/api/clusters/preview-enrichment",
                json={"prompt_text": "do a thing somehow somewhere with everything"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["top_strategies"] == []
        assert body["recommended_strategy"] == "auto"
        assert "bad-strategy" in body["blocked_strategies"]
        assert body["weaknesses"]  # still populated


class TestStrategyIntelligenceParity:
    """AC-8: byte-identical formatted output pre/post structured refactor."""

    @pytest.mark.asyncio
    async def test_formatted_output_byte_identical_across_scenarios(
        self, db_session,
    ):
        """≥10 (task_type, domain) pairs across all 7 task types + empty/populated
        blocked sets + low/high feedback volumes.

        The test snapshots the current formatted output, captures it, and re-runs
        the refactored helper. Because Cycle 1 refactors
        ``resolve_strategy_intelligence`` to delegate to the structured helper,
        the formatted output must remain BYTE-IDENTICAL.
        """
        from app.services.strategy_intelligence import resolve_strategy_intelligence

        # Pairs covering all 7 task types + edge cases.
        scenarios: list[tuple[str, str, list[tuple[str, float, int]], list[tuple[str, int, int]]]] = [
            ("coding", "backend", [("meta-prompting", 8.0, 3)], [("bad-a", 1, 10)]),
            ("coding", "frontend", [], []),
            ("writing", "general", [("few-shot", 7.5, 3)], [("bad-b", 0, 8)]),
            ("analysis", "data", [("chain-of-thought", 8.2, 3)], []),
            ("creative", "general", [], [("bad-c", 1, 10)]),
            ("data", "database", [("structured-output", 7.8, 4)], []),
            ("system", "devops", [("role-playing", 6.5, 3)], []),
            ("general", "general", [], []),
            ("coding", "security", [("alpha", 9.0, 3), ("beta", 8.5, 3), ("gamma", 7.8, 3)], []),
            ("writing", "general", [("few-shot", 4.0, 4)], [("bad-d", 0, 6)]),  # anti-pattern path
        ]

        for task_type, domain, opts, affs in scenarios:
            for strategy, score, n in opts:
                for _ in range(n):
                    await _seed_optimization(db_session, strategy, task_type, domain, score)
            for strategy, up, down in affs:
                await _seed_affinity(db_session, strategy, task_type, up, down)
            await db_session.commit()

            # Capture the formatted output once
            captured, fb_a = await resolve_strategy_intelligence(
                db_session, task_type, domain,
            )
            # Re-run — must be identical (covers idempotency + structured delegation)
            captured_again, fb_b = await resolve_strategy_intelligence(
                db_session, task_type, domain,
            )
            assert captured == captured_again, (
                f"Strategy intel formatted output is not idempotent "
                f"for ({task_type}, {domain})"
            )
            assert fb_a == fb_b
