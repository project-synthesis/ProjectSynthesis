# backend/tests/test_history_pagination_operate_v0_4_15.py
"""v0.4.15 cycle 1 OPERATE — end-to-end pagination correctness.

Live-DB integration test. Seeds 2 projects with mixed status and validates
that the GET /history endpoint returns project-scoped + status-scoped pagination
totals exactly matching per-project counts. Verifies the bug-is-actually-fixed
end-to-end, not just contract-compliance via mocks.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization

pytestmark = pytest.mark.asyncio


def _seed(db: AsyncSession, project_id: str, status: str, n: int) -> None:
    for _ in range(n):
        opt = Optimization(
            id=uuid.uuid4().hex,
            trace_id=uuid.uuid4().hex,
            raw_prompt=f"seed-{project_id}-{status}",
            status=status,
            project_id=project_id,
        )
        db.add(opt)


class TestOperatePaginationEndToEnd:
    """Spec § 8 acceptance criteria 1 + 2 — live verification of project-scoped pagination."""

    async def test_pagination_returns_50_rows_for_project_a_with_more_to_paginate(
        self, app_client, db_session: AsyncSession,
    ):
        # Project A: 75 completed (so 50 returned, 25 remaining)
        # Project B: 30 completed (separate, must not leak into A's totals)
        # Mixed: 20 failed across both (must be filtered out by status='completed')
        _seed(db_session, project_id="A", status="completed", n=75)
        _seed(db_session, project_id="B", status="completed", n=30)
        _seed(db_session, project_id="A", status="failed", n=10)
        _seed(db_session, project_id="B", status="failed", n=10)
        await db_session.commit()

        # Project A page 1
        resp = await app_client.get(
            "/api/history?project_id=A&status=completed&limit=50",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 75, f"expected 75, got {body['total']}"
        assert body["count"] == 50, f"expected 50 in page, got {body['count']}"
        assert body["has_more"] is True, "expected more pages available"
        assert body["next_offset"] == 50
        assert all(item["project_id"] == "A" for item in body["items"])
        assert all(item["status"] == "completed" for item in body["items"])

        # Project A page 2
        resp2 = await app_client.get(
            "/api/history?project_id=A&status=completed&limit=50&offset=50",
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["total"] == 75
        assert body2["count"] == 25, f"expected 25 in final page, got {body2['count']}"
        assert body2["has_more"] is False
        assert body2["next_offset"] is None

        # Project B (no leakage from A's data)
        resp_b = await app_client.get(
            "/api/history?project_id=B&status=completed&limit=50",
        )
        assert resp_b.status_code == 200
        body_b = resp_b.json()
        assert body_b["total"] == 30
        assert body_b["count"] == 30
        assert all(item["project_id"] == "B" for item in body_b["items"])
