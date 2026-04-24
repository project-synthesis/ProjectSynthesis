"""/api/health surfaces per-profile enrichment effectiveness (E2 — #9).

Confirms the endpoint renders the aggregate built by
``OptimizationService.get_enrichment_profile_effectiveness()`` under the
``enrichment_effectiveness`` top-level field.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models import Optimization

pytestmark = pytest.mark.asyncio


async def _seed_profile_rows(db_session) -> None:
    """Seed 4 completed rows across code_aware + cold_start profiles."""
    rows = [
        Optimization(
            id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            raw_prompt=f"Prompt {i}",
            status="completed",
            overall_score=score,
            improvement_score=improvement,
            context_sources={"enrichment_meta": {"enrichment_profile": profile}},
        )
        for i, (score, improvement, profile) in enumerate([
            (8.0, 2.0, "code_aware"),
            (7.5, 1.5, "code_aware"),
            (6.5, 0.8, "cold_start"),
            (7.0, 1.2, "cold_start"),
        ])
    ]
    for row in rows:
        db_session.add(row)
    await db_session.commit()


async def test_health_exposes_enrichment_effectiveness(app_client, db_session):
    """When profile-tagged rows exist, /api/health renders them under
    ``enrichment_effectiveness`` keyed by profile name.
    """
    await _seed_profile_rows(db_session)

    resp = await app_client.get("/api/health?probes=false")
    body = resp.json()

    assert resp.status_code == 200
    ee = body.get("enrichment_effectiveness")
    assert ee is not None, "enrichment_effectiveness missing from response"
    assert set(ee.keys()) == {"code_aware", "cold_start"}

    ca = ee["code_aware"]
    assert ca["count"] == 2
    assert ca["avg_overall_score"] == pytest.approx(7.75, abs=1e-3)
    assert ca["avg_improvement_score"] == pytest.approx(1.75, abs=1e-3)

    cs = ee["cold_start"]
    assert cs["count"] == 2
    assert cs["avg_overall_score"] == pytest.approx(6.75, abs=1e-3)


async def test_health_enrichment_effectiveness_null_when_no_profile_data(app_client):
    """With no profile-tagged rows, the field is rendered as None (not {})."""
    resp = await app_client.get("/api/health?probes=false")
    body = resp.json()
    assert resp.status_code == 200
    # Default fixture's sample_opts seed rows have no enrichment_profile →
    # aggregator returns {} → endpoint maps to None for null-is-default
    # semantics consistent with other optional fields.
    assert body.get("enrichment_effectiveness") is None
