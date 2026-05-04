"""v0.4.15 P0 RED — HistoryPanel pagination correctness via server-pushdown.

Pins spec § 11 binding choices for the backend layers. All tests fail until
GREEN adds the project_id kwarg to OptimizationService.list_optimizations()
and the Query param to GET /history.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization
from app.services.optimization_service import OptimizationService

pytestmark = pytest.mark.asyncio


def _make_opt(
    db: AsyncSession,
    *,
    project_id: str | None,
    status: str = "completed",
    raw_prompt: str = "test",
) -> Optimization:
    """Helper: insert one Optimization row with the given project + status."""
    import uuid
    opt = Optimization(
        id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        raw_prompt=raw_prompt,
        status=status,
        project_id=project_id,
    )
    db.add(opt)
    return opt


class TestServiceFiltersByProjectId:
    """Spec § 11 row 1 — service accepts project_id kwarg, filters rows by it."""

    async def test_list_optimizations_filters_by_project_id(self, db_session: AsyncSession):
        # Seed 3 rows in project A, 2 rows in project B
        for _ in range(3):
            _make_opt(db_session, project_id="A")
        for _ in range(2):
            _make_opt(db_session, project_id="B")
        await db_session.commit()

        svc = OptimizationService(db_session)
        result = await svc.list_optimizations(project_id="A", limit=50)
        assert result["total"] == 3
        assert result["count"] == 3
        assert all(item.project_id == "A" for item in result["items"])

        result_b = await svc.list_optimizations(project_id="B", limit=50)
        assert result_b["total"] == 2
        assert all(item.project_id == "B" for item in result_b["items"])


class TestServiceComposesProjectIdAndStatus:
    """Spec § 11 row 2 — project_id + status compose as intersection."""

    async def test_list_optimizations_project_id_combines_with_status(
        self, db_session: AsyncSession,
    ):
        _make_opt(db_session, project_id="A", status="completed")
        _make_opt(db_session, project_id="A", status="completed")
        _make_opt(db_session, project_id="A", status="failed")
        _make_opt(db_session, project_id="B", status="completed")
        await db_session.commit()

        svc = OptimizationService(db_session)
        result = await svc.list_optimizations(
            project_id="A", status="completed", limit=50,
        )
        assert result["total"] == 2
        assert all(
            item.project_id == "A" and item.status == "completed"
            for item in result["items"]
        )


class TestServiceLegacyKwargsStillWorks:
    """REFACTOR scope item 8 — backward compat. project_id + status omitted = global behavior."""

    async def test_list_optimizations_legacy_no_project_id_returns_all(
        self, db_session: AsyncSession,
    ):
        _make_opt(db_session, project_id="A", status="completed")
        _make_opt(db_session, project_id="B", status="completed")
        _make_opt(db_session, project_id=None, status="failed")
        await db_session.commit()

        svc = OptimizationService(db_session)
        result = await svc.list_optimizations(limit=50)  # No filters at all
        assert result["total"] == 3


class TestRouterAcceptsProjectIdQueryParam:
    """Spec § 11 row 3 — GET /history accepts project_id as Query param.

    Smoke check via TestClient — uses the conftest ``app_client`` + ``db_session``
    fixtures (both back the same in-memory DB via ``override_get_db``), so
    rows added through ``db_session`` are visible to the router that runs
    inside the same request.
    """

    async def test_get_history_accepts_project_id_query_param(
        self, app_client, db_session: AsyncSession,
    ):
        # Seed 2 rows in project A AND 3 rows in project B (contamination).
        # Pre-fix the router accepts `project_id=A` but ignores it (FastAPI
        # silently drops unknown query params), so an unfiltered query returns
        # all 5 rows including B's — exposing the leak.
        import uuid
        for _ in range(2):
            opt = Optimization(
                id=uuid.uuid4().hex,
                trace_id=uuid.uuid4().hex,
                raw_prompt="t",
                status="completed",
                project_id="A",
            )
            db_session.add(opt)
        for _ in range(3):
            opt = Optimization(
                id=uuid.uuid4().hex,
                trace_id=uuid.uuid4().hex,
                raw_prompt="t",
                status="completed",
                project_id="B",
            )
            db_session.add(opt)
        await db_session.commit()

        resp = await app_client.get("/api/history?project_id=A&status=completed&limit=50")
        assert resp.status_code == 200
        body = resp.json()
        assert all(item["project_id"] == "A" for item in body["items"])
        assert body["total"] == 2


class TestRouterTotalReflectsProjectScope:
    """Spec § 11 row 4 — paginated total reflects PROJECT-scoped count, not global."""

    async def test_get_history_total_reflects_project_scope(
        self, app_client, db_session: AsyncSession,
    ):
        # Seed: 3 completed in A, 4 completed in B, 1 failed in A
        import uuid
        seed_data = [
            ("A", "completed"),
            ("A", "completed"),
            ("A", "completed"),
            ("B", "completed"),
            ("B", "completed"),
            ("B", "completed"),
            ("B", "completed"),
            ("A", "failed"),
        ]
        for project_id, status in seed_data:
            opt = Optimization(
                id=uuid.uuid4().hex,
                trace_id=uuid.uuid4().hex,
                raw_prompt="t",
                status=status,
                project_id=project_id,
            )
            db_session.add(opt)
        await db_session.commit()

        # Project A + completed → expect total=3
        resp_a = await app_client.get(
            "/api/history?project_id=A&status=completed&limit=50",
        )
        assert resp_a.status_code == 200
        assert resp_a.json()["total"] == 3

        # Project B + completed → expect total=4
        resp_b = await app_client.get(
            "/api/history?project_id=B&status=completed&limit=50",
        )
        assert resp_b.status_code == 200
        assert resp_b.json()["total"] == 4
