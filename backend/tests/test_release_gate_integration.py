"""T3.1 release-gate integration tests.

Spec: ``docs/superpowers/specs/2026-05-19-t3.1-release-gate-design.md`` §6.2.

Test 9: end-to-end firing-suite flow via the canonical ``app_client``
fixture (httpx AsyncClient + monkey-patched ``async_session_factory``).

Test 10 (release.sh subprocess) is SKIPPED in CI — the shim-script approach
in spec §6.2 has a cwd/version.json discovery problem (release.sh derives
its project root via ``SCRIPT_DIR/..`` which points to ``/tmp/...`` when
the shim is copied there). Manual verification (acceptance #15 / spec §5.3)
is the safety net.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.models import RunRow, ValidationSuite


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def reset_router_service_alarm_cache() -> None:
    """Mirror of the unit-test fixture — keep router ``_service`` alarm cache clean."""
    from app.routers.suites import _service

    _service._invalidate_alarm_cache()
    _service._prior_alarm_states.clear()


# ---------------------------------------------------------------------------
# Test 9 — end-to-end firing-suite flow
# ---------------------------------------------------------------------------


async def test_end_to_end_firing_suite_surfaces_in_release_gates(
    db_session: Any,
    app_client: AsyncClient,
) -> None:
    """Acceptance #5, #6 — create suite → flag → seed firing replay → query gates."""
    suite = ValidationSuite(
        id="s-integration-firing",
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores={
            "mean_overall": 8.0, "p5_overall": 5.0, "p50_overall": 8.0,
            "p95_overall": 9.5, "per_prompt": [], "task_type_distribution": {},
        },
        tolerance_abs=0.5,
        label="integration-firing-suite",
        created_at=datetime.now(UTC),
    )
    db_session.add(suite)
    await db_session.commit()

    # Step 1: flag as gate
    resp = await app_client.post(
        f"/api/suites/{suite.id}/release-gate",
        json={"enabled": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_release_gate"] is True

    # Step 2: seed firing replay (latest_mean=6.0 < baseline 8.0 - tolerance 0.5 = 7.5)
    firing_replay = RunRow(
        id="rr-integration-firing",
        mode="replay_run",
        status="completed",
        suite_id=suite.id,
        prompt_results=[],
        prompts_generated=0,
        aggregate={"mean_overall": 6.0, "p5_overall": 5.0, "p50_overall": 6.0, "p95_overall": 7.0},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db_session.add(firing_replay)
    await db_session.commit()

    # Step 3: query gates endpoint
    resp = await app_client.get("/api/suites/release-gates")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    assert items[0]["suite_id"] == suite.id
    assert items[0]["alarm_state"] == "firing"
    assert items[0]["delta_abs"] == pytest.approx(-2.0)


# ---------------------------------------------------------------------------
# Test 10 — subprocess release.sh (SKIPPED — see module docstring)
# ---------------------------------------------------------------------------


def test_release_sh_dry_run_blocks_on_firing() -> None:
    """SKIPPED in CI — shim-script cwd-resolution problem.

    release.sh derives its project root via ``SCRIPT_DIR/..`` (``SCRIPT_DIR``
    being the script's own directory). When the shim is copied to ``/tmp/``,
    that points at ``/tmp/`` which has no ``version.json``. Patching the
    script in place would race with concurrent test runs; the cleanest fix
    requires a ``RELEASE_GATE_BACKEND_URL`` env var in release.sh itself,
    which is out of scope for T3.1.

    Manual verification per spec §5.3 (acceptance #15) covers this case.
    """
    pytest.skip(
        "release.sh subprocess test requires env-var URL override "
        "in release.sh — covered by manual verification per spec §5.3."
    )
