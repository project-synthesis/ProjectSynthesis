"""Tests for /api/runs endpoints — Foundation P3 cat 9, 7 tests.

Uses async patterns (httpx.AsyncClient via ``app_client`` fixture +
AsyncSession via ``db_session``) matching the conftest convention used
elsewhere in this suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow

pytestmark = pytest.mark.asyncio


async def test_get_runs_pagination_envelope(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/runs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert {"total", "count", "offset", "items", "has_more", "next_offset"}.issubset(body.keys())


async def test_get_runs_filter_by_mode(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(id="r-probe", mode="topic_probe", status="completed", started_at=datetime.utcnow()))
    db_session.add(RunRow(id="r-seed", mode="seed_agent", status="completed", started_at=datetime.utcnow()))
    await db_session.commit()

    resp = await app_client.get("/api/runs?mode=topic_probe")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert "r-probe" in ids and "r-seed" not in ids


async def test_get_runs_filter_by_status(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(id="r-running", mode="topic_probe", status="running", started_at=datetime.utcnow()))
    db_session.add(RunRow(id="r-failed", mode="topic_probe", status="failed", started_at=datetime.utcnow()))
    await db_session.commit()

    resp = await app_client.get("/api/runs?status=failed")
    assert resp.status_code == 200
    statuses = {r["status"] for r in resp.json()["items"]}
    assert statuses == {"failed"}


async def test_get_runs_filter_by_project_id(app_client: AsyncClient, db_session: AsyncSession) -> None:
    from app.models import PromptCluster
    proj = PromptCluster(id="proj-x", state="project", label="x")
    db_session.add(proj)
    db_session.add(RunRow(
        id="r-with-proj", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(), project_id="proj-x",
    ))
    db_session.add(RunRow(
        id="r-no-proj", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(),
    ))
    await db_session.commit()

    resp = await app_client.get("/api/runs?project_id=proj-x")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["items"]]
    assert ids == ["r-with-proj"]


async def test_get_runs_ordered_started_at_desc(app_client: AsyncClient, db_session: AsyncSession) -> None:
    base = datetime.utcnow()
    for i in range(3):
        db_session.add(RunRow(
            id=f"r-{i}", mode="topic_probe", status="completed",
            started_at=base - timedelta(minutes=i),
        ))
    await db_session.commit()

    resp = await app_client.get("/api/runs?limit=3")
    items = resp.json()["items"]
    ids = [r["id"] for r in items]
    assert ids == ["r-0", "r-1", "r-2"]  # newest first


async def test_get_run_by_id_returns_full_detail(app_client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(RunRow(
        id="r-detail", mode="topic_probe", status="completed",
        started_at=datetime.utcnow(),
        topic="testtopic", topic_probe_meta={"scope": "**/*"},
    ))
    await db_session.commit()

    resp = await app_client.get("/api/runs/r-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "r-detail"
    assert body["topic"] == "testtopic"
    assert body["topic_probe_meta"] == {"scope": "**/*"}


async def test_get_run_by_id_404_on_miss(app_client: AsyncClient) -> None:
    resp = await app_client.get("/api/runs/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"


# v0.4.32 — DELETE + PATCH endpoint tests


async def test_delete_run_204(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    row = RunRow(id="rr-del-1", mode="topic_probe", topic="t")
    db_session.add(row)
    await db_session.commit()
    resp = await app_client.delete("/api/runs/rr-del-1")
    assert resp.status_code == 204
    # GET /api/runs/{id} now 404
    follow = await app_client.get("/api/runs/rr-del-1")
    assert follow.status_code == 404


async def test_delete_run_404(app_client: AsyncClient) -> None:
    resp = await app_client.delete("/api/runs/nonexistent")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Run not found"}


async def test_delete_run_with_validation_suite_fk_nulls_suite(
    app_client: AsyncClient,
    db_session: AsyncSession,
    enable_sqlite_foreign_keys: AsyncSession,
) -> None:
    """Pins AC #4a: ValidationSuite.source_run_id has ondelete=SET NULL.
    Deleting a referenced RunRow nulls the suite's source_run_id; the
    suite + its replays + baselines stay intact.
    """
    from app.models import ValidationSuite

    rr = RunRow(id="rr-fk-1", mode="topic_probe", topic="t")
    suite = ValidationSuite(
        id="vs-fk-1",
        source_run_id="rr-fk-1",
        prompts_snapshot=[{"id": "p1", "raw_prompt": "x"}],
        baseline_scores={"overall": 7.0},
        label="suite-1",
    )
    db_session.add_all([rr, suite])
    await db_session.commit()

    resp = await app_client.delete("/api/runs/rr-fk-1")
    assert resp.status_code == 204

    # Suite still exists; source_run_id now NULL
    db_session.expire_all()
    refreshed = await db_session.get(ValidationSuite, "vs-fk-1")
    assert refreshed is not None
    assert refreshed.source_run_id is None


async def test_patch_run_sets_display_name(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    row = RunRow(id="rr-pat-1", mode="topic_probe", topic="orig topic")
    db_session.add(row)
    await db_session.commit()
    resp = await app_client.patch(
        "/api/runs/rr-pat-1", json={"display_name": "My Custom Label"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "My Custom Label"
    assert body["topic"] == "orig topic"  # topic preserved


async def test_patch_run_empty_string_clears_rename(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    row = RunRow(
        id="rr-pat-2", mode="topic_probe", topic="t", display_name="Old Name"
    )
    db_session.add(row)
    await db_session.commit()
    resp = await app_client.patch(
        "/api/runs/rr-pat-2", json={"display_name": ""}
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] is None


async def test_patch_run_over_200_chars_returns_400(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    row = RunRow(id="rr-pat-3", mode="topic_probe", topic="t")
    db_session.add(row)
    await db_session.commit()
    resp = await app_client.patch(
        "/api/runs/rr-pat-3", json={"display_name": "x" * 201}
    )
    assert resp.status_code == 422  # Pydantic validation = 422 in FastAPI


async def test_patch_run_404(app_client: AsyncClient) -> None:
    resp = await app_client.patch(
        "/api/runs/nonexistent", json={"display_name": "x"}
    )
    assert resp.status_code == 404
