"""v0.4.32 — bulk-delete + bulk-export endpoint tests."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow

pytestmark = pytest.mark.asyncio


async def test_bulk_delete_all_found(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add_all([
        RunRow(id=f"bd-{i}", mode="topic_probe", topic=f"t{i}") for i in range(3)
    ])
    await db_session.commit()

    resp = await app_client.post(
        "/api/runs/bulk-delete", json={"ids": ["bd-0", "bd-1", "bd-2"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["deleted"]) == {"bd-0", "bd-1", "bd-2"}
    assert body["not_found"] == []


async def test_bulk_delete_partial_found(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add(RunRow(id="bd-real", mode="topic_probe", topic="t"))
    await db_session.commit()

    resp = await app_client.post(
        "/api/runs/bulk-delete",
        json={"ids": ["bd-real", "bd-missing-1", "bd-missing-2"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == ["bd-real"]
    assert set(body["not_found"]) == {"bd-missing-1", "bd-missing-2"}


async def test_bulk_delete_empty_ids_returns_422(
    app_client: AsyncClient,
) -> None:
    resp = await app_client.post("/api/runs/bulk-delete", json={"ids": []})
    assert resp.status_code == 422  # Pydantic min_length=1


async def test_bulk_delete_over_200_ids_returns_422(
    app_client: AsyncClient,
) -> None:
    resp = await app_client.post(
        "/api/runs/bulk-delete", json={"ids": [f"id-{i}" for i in range(201)]}
    )
    assert resp.status_code == 422  # Pydantic max_length=200


async def test_bulk_export_returns_full_run_results(
    app_client: AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add_all([
        RunRow(
            id=f"be-{i}",
            mode="topic_probe",
            topic=f"topic-{i}",
            display_name=f"Label {i}",
        ) for i in range(2)
    ])
    await db_session.commit()

    resp = await app_client.post(
        "/api/runs/bulk-export", json={"ids": ["be-0", "be-1", "be-missing"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2  # missing id silently omitted
    by_id = {row["id"]: row for row in body}
    assert by_id["be-0"]["display_name"] == "Label 0"
    assert by_id["be-0"]["topic"] == "topic-0"
