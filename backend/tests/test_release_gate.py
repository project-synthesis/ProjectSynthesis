"""T3.1 release-gate unit tests.

Spec: ``docs/superpowers/specs/2026-05-19-t3.1-release-gate-design.md`` §6.1.

Eight unit tests pinning the functional acceptance criteria for the
release-gate API surface + route ordering. Tests use the canonical
``app_client`` fixture from ``conftest.py`` which monkey-patches
``async_session_factory`` so service-layer reads see the test ``db_session``.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.models import RunRow, ValidationSuite
from app.services.validation_suite_service import ValidationSuiteService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_router_service_alarm_cache() -> None:
    """Reset the router's module-level ``_service`` alarm cache before each test.

    ``routers/suites.py:116`` defines a process-wide ``_service =
    ValidationSuiteService()`` whose ``_alarm_cache`` (30s TTL) and
    ``_prior_alarm_states`` would otherwise carry state across tests in this
    file. Each test seeds different replay data, so prior cached state would
    mask a `firing` suite as `nominal` (the symptom that surfaced in rev-2
    of the test suite). The service exposes ``_invalidate_alarm_cache()`` as
    a documented test seam at ``validation_suite_service.py:531``.
    """
    from app.routers.suites import _service

    _service._invalidate_alarm_cache()
    _service._prior_alarm_states.clear()


@pytest.fixture
async def suite(db_session: Any) -> ValidationSuite:
    """Insert one ValidationSuite into the test DB; return it for assertions."""
    suite = ValidationSuite(
        id="suite-t3-1-a",
        source_run_id=None,
        prompts_snapshot=[{"raw_prompt": "test", "intent_label": None, "original_optimization_id": None}],
        baseline_scores={
            "mean_overall": 7.5,
            "p5_overall": 5.0,
            "p50_overall": 7.5,
            "p95_overall": 9.0,
            "per_prompt": [{"raw_prompt_idx": 0, "overall": 7.5, "dimensions": {}}],
            "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="T3.1 fixture suite",
        project_id=None,
        repo_full_name=None,
        created_at=datetime.now(UTC),
    )
    db_session.add(suite)
    await db_session.commit()
    return suite


# ---------------------------------------------------------------------------
# Test 1 — column default + GET surfacing
# ---------------------------------------------------------------------------


async def test_is_release_gate_defaults_false_and_surfaces_in_get(
    app_client: AsyncClient,
    suite: ValidationSuite,
) -> None:
    """Acceptance #1, #2 — new column defaults False; GET surfaces it."""
    resp = await app_client.get(f"/api/suites/{suite.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_release_gate"] is False


# ---------------------------------------------------------------------------
# Test 2 — POST enabled=true flips column on; returns updated suite
# ---------------------------------------------------------------------------


async def test_post_release_gate_enabled_true_flips_on(
    app_client: AsyncClient,
    suite: ValidationSuite,
) -> None:
    """Acceptance #3 — POST {enabled: true} flips column True."""
    resp = await app_client.post(
        f"/api/suites/{suite.id}/release-gate",
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_release_gate"] is True
    assert body["id"] == suite.id


# ---------------------------------------------------------------------------
# Test 3 — POST enabled=false; idempotent on already-False
# ---------------------------------------------------------------------------


async def test_post_release_gate_enabled_false_is_idempotent(
    app_client: AsyncClient,
    suite: ValidationSuite,
) -> None:
    """Acceptance #3 — toggling off when already off is a no-op (200, False)."""
    resp = await app_client.post(
        f"/api/suites/{suite.id}/release-gate",
        json={"enabled": False},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_release_gate"] is False


# ---------------------------------------------------------------------------
# Test 4 — 404 on missing suite
# ---------------------------------------------------------------------------


async def test_post_release_gate_404_on_missing_suite(app_client: AsyncClient) -> None:
    """Acceptance #3 — 404 with ``suite_not_found`` envelope."""
    resp = await app_client.post(
        "/api/suites/does-not-exist-xyz/release-gate",
        json={"enabled": True},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": "suite_not_found"}


# ---------------------------------------------------------------------------
# Test 5 — list endpoint filters to active flagged suites
# ---------------------------------------------------------------------------


async def test_list_release_gates_filters_to_active_flagged(
    db_session: Any,
    app_client: AsyncClient,
) -> None:
    """Acceptance #4, #6 — only is_release_gate=True AND retired_at IS NULL."""
    flagged_active = ValidationSuite(
        id="s-flagged-active",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 7.0, "p5_overall": 5.0, "p50_overall": 7.0,
            "p95_overall": 9.0, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="flagged-active",
        is_release_gate=True,
        created_at=datetime.now(UTC),
    )
    flagged_retired = ValidationSuite(
        id="s-flagged-retired",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 7.0, "p5_overall": 5.0, "p50_overall": 7.0,
            "p95_overall": 9.0, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="flagged-retired",
        is_release_gate=True,
        retired_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    unflagged_active = ValidationSuite(
        id="s-unflagged-active",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 7.0, "p5_overall": 5.0, "p50_overall": 7.0,
            "p95_overall": 9.0, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="unflagged-active",
        is_release_gate=False,
        created_at=datetime.now(UTC),
    )
    db_session.add_all([flagged_active, flagged_retired, unflagged_active])
    await db_session.commit()

    resp = await app_client.get("/api/suites/release-gates")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    ids = {item["suite_id"] for item in items}
    assert ids == {"s-flagged-active"}


# ---------------------------------------------------------------------------
# Test 6 — alarm_state maps firing/nominal correctly
# ---------------------------------------------------------------------------


async def test_list_release_gates_maps_firing_and_nominal(
    db_session: Any,
    app_client: AsyncClient,
) -> None:
    """Acceptance #5 — entry in latest_alarms ⇒ alarm_state='firing'; absent ⇒ 'nominal'."""
    firing_suite = ValidationSuite(
        id="s-firing",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 8.0, "p5_overall": 5.0, "p50_overall": 8.0,
            "p95_overall": 9.5, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="firing-suite",
        is_release_gate=True,
        created_at=datetime.now(UTC),
    )
    nominal_suite = ValidationSuite(
        id="s-nominal",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 7.0, "p5_overall": 5.0, "p50_overall": 7.0,
            "p95_overall": 8.5, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="nominal-suite",
        is_release_gate=True,
        created_at=datetime.now(UTC),
    )
    firing_replay = RunRow(
        id="rr-firing",
        mode="replay_run",
        status="completed",
        suite_id="s-firing",
        prompt_results=[],
        prompts_generated=0,
        aggregate={"mean_overall": 6.0, "p5_overall": 5.0, "p50_overall": 6.0, "p95_overall": 7.0},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db_session.add_all([firing_suite, nominal_suite, firing_replay])
    await db_session.commit()

    resp = await app_client.get("/api/suites/release-gates")
    assert resp.status_code == 200, resp.text
    items = {item["suite_id"]: item for item in resp.json()}
    assert items["s-firing"]["alarm_state"] == "firing"
    assert items["s-firing"]["baseline_mean"] == pytest.approx(8.0)
    assert items["s-firing"]["latest_mean"] == pytest.approx(6.0)
    assert items["s-firing"]["delta_abs"] == pytest.approx(-2.0)
    assert items["s-firing"]["latest_replay_at"] is not None
    assert items["s-nominal"]["alarm_state"] == "nominal"
    assert items["s-nominal"]["baseline_mean"] is None
    assert items["s-nominal"]["latest_mean"] is None
    assert items["s-nominal"]["delta_abs"] is None
    assert items["s-nominal"]["latest_replay_at"] is None


# ---------------------------------------------------------------------------
# Test 7 — empty flagged set returns []; compute_regression_alarm NOT called
# ---------------------------------------------------------------------------


async def test_list_release_gates_empty_skips_alarm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance #4 — when no flagged suites, return []; skip alarm call.

    Constructs a fresh ``ValidationSuiteService`` (NOT the app singleton) so
    the alarm-cache state is isolated. The service's internal
    ``async_session_factory()`` opens the real DB session — fine because we
    expect zero flagged suites in any non-fixture DB.
    """
    service = ValidationSuiteService()

    calls = {"count": 0}

    async def spy_compute_regression_alarm(*args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        from app.schemas.validation_suite import RegressionAlarmBlock
        return RegressionAlarmBlock(suites_total=0, suites_in_alarm=0, latest_alarms=[])

    monkeypatch.setattr(service, "compute_regression_alarm", spy_compute_regression_alarm)

    result = await service.list_release_gates()
    assert result == []
    assert calls["count"] == 0, (
        "compute_regression_alarm must NOT be called when there are zero "
        "flagged suites (optimization branch per spec §3.2)"
    )


# ---------------------------------------------------------------------------
# Test 8 — route ordering: /release-gates beats /{suite_id}
# ---------------------------------------------------------------------------


async def test_route_ordering_release_gates_matches_literal(app_client: AsyncClient) -> None:
    """Acceptance #6 — ASGI router matches /api/suites/release-gates to
    list_release_gates handler, NOT to get_suite with suite_id='release-gates'.

    If route ordering is wrong, this returns 404 (suite_not_found) instead of
    200 with a JSON array.
    """
    resp = await app_client.get("/api/suites/release-gates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list), (
        f"Expected JSON array (list endpoint); got {type(body).__name__}. "
        "Route ordering bug — release-gates was captured as suite_id."
    )
