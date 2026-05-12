"""RED-phase tests for ``app/routers/suites.py`` — Topic Probe Tier 2 Cycle 5 (15 tests).

Plan:  ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 5 Task 5.1
Spec:  ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §4 (REST
       surface + error envelope + 10 codes) + §10 Cycle 5

The router does NOT exist yet — every test fails with HTTP 404 (route not
registered, since ``app/main.py`` wraps the import in ``try/except
ImportError`` per the codebase convention at lines 2046-2050). Once Cycle 5
GREEN lands ``app/routers/suites.py`` + a ``main.py`` registration block,
every test must pass without any further test-file edit (RED-phase invariant
per ``feedback_tdd_protocol.md``).

Surface contract pinned by these tests (spec §4):

* ``POST /api/probes/{run_id}/save-as-suite``  → 201 / 400 / 404 / 409 / 429
  (rate-limit 20/min); body ``SaveSuiteRequest{label, tolerance_abs?}``
* ``POST /api/suites/{suite_id}/replay``       → 202 / 404 / 409 / 429
  (rate-limit 5/min); placeholder dispatch INSERTs the initial
  ``RunRow(mode='replay_run', status='running', suite_id=...)`` and returns
  immediately — no generator spawn (Cycle 5 GREEN-step shape per spec §10
  Cycle 5; full spawn lands in Cycle 6+8)
* ``GET /api/suites``                          → 200; paginated envelope
* ``GET /api/suites/{suite_id}``               → 200 / 404
* ``GET /api/suites/{suite_id}/replays``       → 200; paginated envelope
* ``POST /api/suites/{suite_id}/retire``       → 200 / 400 / 404; idempotent

Error envelope follows the codebase convention ``{"detail": "<code>"}``
(FastAPI default — see ``routers/probes.py`` precedent at lines 170/173/186/321);
spec §4's "{code, message}" wording describes the logical envelope, not the
wire format. The 10 codes enumerated in spec §4 lines 337-346 are exercised
by tests 2-5, 9, 12, 15 below.

Scope hardness (§14): no PATCH /api/suites/{id} route — suites are immutable
after creation; only the retire soft-delete transitions ``retired_at`` /
``retired_reason``. Test 15 includes a 405-method-not-allowed assertion to
pin this immutability.

Cycle 5 explicitly DOES NOT test end-to-end replay completion — that lands
in Cycle 8 once ``dispatch_async()`` + ``ReplayRunGenerator`` registration
both exist. The replay tests here cover ONLY the 202 response shape, the
initial INSERT, suite-state preconditions (404 missing / 409 retired), and
the 5/min rate limit (spec §10 Cycle 5 attention table).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow, ValidationSuite

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures + seeding helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_rate_limit() -> Any:
    """Reset the in-memory rate-limit storage before/after each test.

    Matches the canonical pattern in ``tests/test_probe_router.py:69-76`` so
    rate-limit budgets from one test never leak into the next.
    """
    from app.dependencies.rate_limit import reset_rate_limit_storage

    reset_rate_limit_storage()
    yield
    reset_rate_limit_storage()


def _canonical_aggregate(mean: float = 7.85) -> dict:
    """Canonical ``RunRow.aggregate`` shape matching the topic_probe generator.

    Mirrors ``tests/test_validation_suite_service.py:_canonical_aggregate``
    so the save-as-suite path sees the exact production shape — keys
    ``mean_overall`` / ``p5_overall`` / ``p50_overall`` / ``p95_overall``
    are mandatory; ``per_prompt`` carries 3 rows.
    """
    return {
        "mean_overall": mean,
        "p5_overall": 6.20,
        "p50_overall": mean,
        "p95_overall": 9.10,
        "completed_count": 3,
        "failed_count": 0,
        "f5_flag_fires": 0,
        "scoring_formula_version": 4,
        "task_type_distribution": {"coding": 2, "analysis": 1},
        "per_prompt": [
            {
                "raw_prompt_idx": 0, "overall": 8.1,
                "dimensions": {"clarity": 8.0, "specificity": 8.5,
                               "structure": 7.8, "faithfulness": 8.2,
                               "conciseness": 7.9},
            },
            {
                "raw_prompt_idx": 1, "overall": 7.4,
                "dimensions": {"clarity": 7.2, "specificity": 7.8,
                               "structure": 7.0, "faithfulness": 7.5,
                               "conciseness": 7.4},
            },
            {
                "raw_prompt_idx": 2, "overall": 8.0,
                "dimensions": {"clarity": 8.1, "specificity": 8.0,
                               "structure": 7.9, "faithfulness": 8.0,
                               "conciseness": 8.0},
            },
        ],
    }


def _prompt_results(n: int = 3) -> list[dict]:
    """N canonical ``RunRow.prompt_results`` rows — position-aligned with
    ``aggregate.per_prompt``."""
    return [
        {
            "prompt_idx": i,
            "raw_prompt": f"raw prompt {i}",
            "optimized_prompt": f"optimized prompt {i}",
            "intent_label": "general",
            "overall_score": 7.0 + (i % 3) * 0.5,
            "optimization_id": None,
            "status": "completed",
        }
        for i in range(n)
    ]


async def _seed_completed_probe_run(
    db: AsyncSession,
    *,
    run_id: str | None = None,
    mode: str = "topic_probe",
    status: str = "completed",
    aggregate: dict | None = None,
) -> str:
    """Insert a ``RunRow`` and return its id.

    Default: a completed topic_probe row with a canonical aggregate — the
    happy-path shape that ``ValidationSuiteService.create_from_run`` accepts
    without raising. Callers override ``mode`` / ``status`` / ``aggregate``
    to exercise the various 4xx/409 branches.
    """
    pid = run_id or uuid.uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    row = RunRow(
        id=pid,
        mode=mode,
        status=status,
        started_at=now,
        completed_at=now if status != "running" else None,
        prompts_generated=3,
        prompt_results=_prompt_results(3),
        aggregate=aggregate if aggregate is not None else _canonical_aggregate(),
        repo_full_name="acme/widget",
    )
    db.add(row)
    await db.commit()
    return pid


async def _seed_suite_via_service(
    db: AsyncSession,
    *,
    label: str = "router-fixture-suite",
    tolerance_abs: float = 0.5,
) -> str:
    """Create a ``ValidationSuite`` via the service so router tests share the
    canonical persistence path. Returns the suite_id.

    The service routes its terminal write through ``app.state.write_queue``,
    which conftest's ``app_client`` fixture wires to the in-memory db_session
    so writes are immediately visible.
    """
    from app.services.validation_suite_service import ValidationSuiteService

    run_id = await _seed_completed_probe_run(db)
    service = ValidationSuiteService()
    # The service reads via ``async_session_factory`` — repoint it at db_session
    # for this call. Mirrors the test_validation_suite_service.py pattern but
    # inline since this helper has its own short scope.
    import app.database as database_mod

    class _SessionContext:
        async def __aenter__(self) -> AsyncSession:
            return db

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    original = database_mod.async_session_factory
    database_mod.async_session_factory = lambda: _SessionContext()  # type: ignore[assignment]
    try:
        from app.main import app
        out = await service.create_from_run(
            run_id=run_id,
            label=label,
            tolerance_abs=tolerance_abs,
            db=db,
            write_queue=app.state.write_queue,
        )
    finally:
        database_mod.async_session_factory = original  # type: ignore[assignment]
    return out.id


# ===========================================================================
# Save-as-suite (6 tests)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 1 — happy path: 201 + ValidationSuiteOut shape + DB row exists
# ---------------------------------------------------------------------------


async def test_save_as_suite_happy_path_returns_201_with_validation_suite_out(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """``POST /api/probes/{run_id}/save-as-suite`` on a completed topic_probe
    run returns 201 with a fully-populated ``ValidationSuiteOut`` body and
    persists a ``ValidationSuite`` row.

    Spec §4 line 272: 201 status, 20/min rate limit, response model
    ``ValidationSuiteOut``. The body fields enumerated below match the
    Pydantic schema declaration in ``app/schemas/validation_suite.py:87-110``.
    """
    run_id = await _seed_completed_probe_run(db_session)

    resp = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "happy-suite", "tolerance_abs": 0.5},
    )

    assert resp.status_code == 201, (
        f"expected 201 on save-as-suite happy path, got "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # ``ValidationSuiteOut`` required fields per spec §4 lines 398-415.
    for key in (
        "id", "source_run_id", "label", "tolerance_abs", "project_id",
        "repo_full_name", "created_at", "retired_at", "retired_reason",
        "prompts_snapshot", "baseline_scores",
    ):
        assert key in body, f"missing {key!r} in ValidationSuiteOut body"
    assert body["label"] == "happy-suite"
    assert body["tolerance_abs"] == 0.5
    assert body["source_run_id"] == run_id
    assert body["retired_at"] is None
    assert body["retired_reason"] is None
    assert isinstance(body["prompts_snapshot"], list)
    assert len(body["prompts_snapshot"]) == 3
    # ``baseline_scores`` is a nested ``BaselineScoresPayload`` (spec §4 line 375)
    assert body["baseline_scores"]["mean_overall"] == 7.85
    assert "per_prompt" in body["baseline_scores"]

    # DB row persistence — never trust the response as evidence of write
    # (Foundation P4 OPERATE/O1 canon).
    persisted = (
        await db_session.execute(
            select(ValidationSuite).where(ValidationSuite.id == body["id"])
        )
    ).scalar_one_or_none()
    assert persisted is not None, "ValidationSuite row not persisted"
    assert persisted.label == "happy-suite"
    assert persisted.source_run_id == run_id


# ---------------------------------------------------------------------------
# Test 2 — invalid label: 400 (empty / too long)
# ---------------------------------------------------------------------------


async def test_save_as_suite_invalid_label_returns_400(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Pydantic ``Field(min_length=1, max_length=120)`` on
    :class:`SaveSuiteRequest.label` rejects both empty and oversized labels.

    Spec §4 error code ``invalid_label`` → 400. FastAPI's automatic
    422-for-validation-errors default is overridden via an exception handler
    so the router returns 400 with the canonical envelope, matching the
    codebase convention (``routers/probes.py:186`` ``invalid_request``
    precedent — 400, not 422).
    """
    run_id = await _seed_completed_probe_run(db_session)

    # Empty label
    resp_empty = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "", "tolerance_abs": 0.5},
    )
    assert resp_empty.status_code == 400, (
        f"empty label must yield 400 invalid_label, got "
        f"{resp_empty.status_code}: {resp_empty.text}"
    )
    assert "invalid_label" in resp_empty.text

    # Label too long — 200 chars exceeds the 120-char max
    resp_long = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "x" * 200, "tolerance_abs": 0.5},
    )
    assert resp_long.status_code == 400, (
        f"oversized label must yield 400 invalid_label, got "
        f"{resp_long.status_code}: {resp_long.text}"
    )
    assert "invalid_label" in resp_long.text


# ---------------------------------------------------------------------------
# Test 3 — invalid tolerance: 400 (below 0.1 / above 5.0)
# ---------------------------------------------------------------------------


async def test_save_as_suite_invalid_tolerance_returns_400(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Pydantic ``Field(ge=0.1, le=5.0)`` on
    :class:`SaveSuiteRequest.tolerance_abs` rejects outside-range values.

    Spec §4 error code ``invalid_tolerance`` → 400. The ``[0.1, 5.0]`` band
    is chosen so the regression alarm doesn't trip on insignificant drift
    (<0.1 is below scoring resolution) nor stay permanently nominal
    (>5.0 exceeds the 1-10 dimension scale entirely).
    """
    run_id = await _seed_completed_probe_run(db_session)

    # Below 0.1 — 0.05 is invalid
    resp_low = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "low-tol", "tolerance_abs": 0.05},
    )
    assert resp_low.status_code == 400, (
        f"tolerance < 0.1 must yield 400 invalid_tolerance, got "
        f"{resp_low.status_code}: {resp_low.text}"
    )
    assert "invalid_tolerance" in resp_low.text

    # Above 5.0 — 10.0 is invalid
    resp_high = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "high-tol", "tolerance_abs": 10.0},
    )
    assert resp_high.status_code == 400, (
        f"tolerance > 5.0 must yield 400 invalid_tolerance, got "
        f"{resp_high.status_code}: {resp_high.text}"
    )
    assert "invalid_tolerance" in resp_high.text


# ---------------------------------------------------------------------------
# Test 4 — unknown run_id: 404 run_not_found
# ---------------------------------------------------------------------------


async def test_save_as_suite_run_not_found_returns_404(
    app_client: AsyncClient,
) -> None:
    """A POST against a non-existent ``run_id`` returns 404 with
    ``detail='run_not_found'``.

    Spec §4 error code ``run_not_found`` → 404. The service raises
    ``ValueError("run_not_found")`` (validation_suite_service.py:609); the
    router maps it to a canonical 404 envelope.
    """
    resp = await app_client.post(
        "/api/probes/nonexistent-uuid-xyz/save-as-suite",
        json={"label": "ghost", "tolerance_abs": 0.5},
    )
    assert resp.status_code == 404, (
        f"unknown run_id must yield 404 run_not_found, got "
        f"{resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "run_not_found"


# ---------------------------------------------------------------------------
# Test 5 — 4xx family: running (409) / non-probe (400) / missing aggregate (409)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seed_kwargs, expected_status, expected_detail",
    [
        # Branch 1: run still running — service raises run_not_completed
        # Spec §4 line 338: run_not_completed → 409.
        pytest.param(
            {"status": "running", "mode": "topic_probe",
             "aggregate": _canonical_aggregate()},
            409, "run_not_completed",
            id="running_run",
        ),
        # Branch 2: completed but mode != topic_probe.
        # Spec §4 line 339: not_a_probe_run → 400 (NOT 409 — this is the
        # one outlier in the precondition family). The worker prompt
        # lumped all three branches as 409; we follow spec §4 verbatim
        # since spec is canonical.
        pytest.param(
            {"status": "completed", "mode": "seed_agent",
             "aggregate": _canonical_aggregate()},
            400, "not_a_probe_run",
            id="seed_agent_run",
        ),
        # Branch 3: completed topic_probe but aggregate missing mean_overall.
        # Spec §4 line 340: run_missing_aggregate → 409.
        pytest.param(
            {"status": "completed", "mode": "topic_probe", "aggregate": None},
            409, "run_missing_aggregate",
            id="missing_aggregate",
        ),
    ],
)
async def test_save_as_suite_combined_409s(
    app_client: AsyncClient, db_session: AsyncSession,
    seed_kwargs: dict, expected_status: int, expected_detail: str,
) -> None:
    """4xx precondition family — run not in a save-eligible state.

    Per spec §4 error envelope (lines 337-340) the three save-precondition
    failures map as follows:

      * ``run_not_completed`` → 409 (status != 'completed')
      * ``not_a_probe_run``  → 400 (mode != 'topic_probe')  ← outlier
      * ``run_missing_aggregate`` → 409 (aggregate missing / lacks mean_overall)

    The function name keeps the worker-prompt shape (``combined_409s``) but
    the per-case ``expected_status`` honors the spec — the middle branch is
    a 400 because a non-probe run is structurally wrong for this endpoint
    (you're calling save-as-suite on the wrong resource type), not a
    state-conflict failure.
    """
    run_id = await _seed_completed_probe_run(db_session, **seed_kwargs)
    resp = await app_client.post(
        f"/api/probes/{run_id}/save-as-suite",
        json={"label": "blocked", "tolerance_abs": 0.5},
    )
    assert resp.status_code == expected_status, (
        f"branch {expected_detail!r} must yield {expected_status}, got "
        f"{resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == expected_detail


# ---------------------------------------------------------------------------
# Test 6 — rate limit 20/min: 21st+ POST returns 429
# ---------------------------------------------------------------------------


async def test_save_as_suite_rate_limit_20_per_minute_returns_429_after_threshold(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec §4 line 272 — 20/min rate limit on ``POST /api/probes/{id}/save-as-suite``.

    Issuing 25 valid POSTs in quick succession must trip the limit at the
    21st request — the first 20 are 201 (or 409 if the underlying row gets
    re-used; the seeded run is re-savable since save-as-suite is idempotent
    at the run level: you can fork the same run into multiple distinct
    suites), and the 21st+ are 429.

    Per the canonical pattern (``tests/test_bulk_delete_router.py:146-166``)
    we assert that the 21st status is 429 — not that every request before it
    is 201 — since the limit hits AT the threshold regardless of how
    many succeed first.
    """
    # Pre-seed 25 distinct completed runs so each POST has a unique target
    # (avoids accidental request-collapsing on identical paths under retry).
    run_ids = [
        await _seed_completed_probe_run(db_session)
        for _ in range(25)
    ]

    statuses: list[int] = []
    for i, run_id in enumerate(run_ids):
        resp = await app_client.post(
            f"/api/probes/{run_id}/save-as-suite",
            json={"label": f"rl-{i}", "tolerance_abs": 0.5},
        )
        statuses.append(resp.status_code)

    # First 20 must succeed (201). The 21st must be 429.
    assert statuses[20] == 429, (
        f"21st request must be 429 per the 20/min limit; got "
        f"{statuses[20]} (full sequence: {statuses})"
    )
    # Sanity: at least one of the first 20 must have been a successful 201
    # — the limit only matters if writes actually happened.
    assert any(s == 201 for s in statuses[:20]), (
        f"no 201 in first 20 requests — limit assertion meaningless: "
        f"{statuses[:20]}"
    )


# ===========================================================================
# Replay (4 tests) — placeholder dispatch path per spec §10 Cycle 5
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 7 — replay 202 + ReplayRunOut body + Location / Retry-After headers
# ---------------------------------------------------------------------------


async def test_replay_returns_202_with_replay_run_out_body_and_location_retry_after_headers(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec §4 line 276 — replay returns 202 Accepted with a
    :class:`ReplayRunOut` body plus ``Location`` + ``Retry-After`` headers.

    The ReplayRunOut shape per spec §4 lines 426-442:
      ``run_id`` (str), ``suite_id`` (str), ``mode='replay_run'``,
      ``status='running'``, ``started_at`` (datetime), ``poll_url`` (str).

    Cycle 5 GREEN-step (per spec §10 Cycle 5) ships the 202 + initial INSERT
    only — the generator does NOT spawn yet. End-to-end replay completion
    lands in Cycle 8 once ``dispatch_async()`` + ``ReplayRunGenerator``
    registration both exist.

    ``Location`` header must equal ``poll_url`` (RFC 7240 + spec §4 line 290
    precedent block). ``Retry-After`` is an integer-seconds hint to the
    polling client.
    """
    suite_id = await _seed_suite_via_service(db_session)

    resp = await app_client.post(f"/api/suites/{suite_id}/replay")

    assert resp.status_code == 202, (
        f"replay must return 202 Accepted, got "
        f"{resp.status_code}: {resp.text}"
    )

    body = resp.json()
    # Body shape: every field of ReplayRunOut must be present.
    for key in ("run_id", "suite_id", "mode", "status", "started_at", "poll_url"):
        assert key in body, f"missing {key!r} in ReplayRunOut body"
    assert body["suite_id"] == suite_id
    assert body["mode"] == "replay_run"
    assert body["status"] == "running"
    assert isinstance(body["run_id"], str) and len(body["run_id"]) >= 16
    assert body["poll_url"], "poll_url must be a non-empty string"

    # Header contract: Location mirrors poll_url; Retry-After is an integer.
    assert resp.headers.get("Location") == body["poll_url"], (
        f"Location header must equal poll_url; got "
        f"Location={resp.headers.get('Location')!r}, "
        f"poll_url={body['poll_url']!r}"
    )
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header must be present"
    assert int(retry_after) >= 1, (
        f"Retry-After must be >= 1 (integer seconds), got {retry_after!r}"
    )


# ---------------------------------------------------------------------------
# Test 8 — replay INSERT: RunRow with mode/status/suite_id set
# ---------------------------------------------------------------------------


async def test_replay_inserts_initial_run_row_mode_replay_run_status_running_suite_id_set(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """The 202 placeholder dispatch INSERTs a ``RunRow`` with
    ``mode='replay_run'``, ``status='running'``, and ``suite_id`` pointing
    back to the source suite.

    Spec §10 Cycle 5 GREEN-step note: "it INSERTs the initial
    ``RunRow(mode='replay_run', status='running', suite_id=...)`` via
    ``WriteQueue.submit()`` and returns 202 immediately, without spawning
    the generator". The orphan ``running`` row is reconciled by the
    existing ``_gc_orphan_runs`` sweep within 1h TTL until Cycle 6+8 land
    the real spawn primitive — Cycle 5 OPERATE confirms the GC handles
    the placeholder.

    DB query is the canonical evidence (Foundation P4 OPERATE/O1) — we
    never trust the 202 response body alone as proof of persistence.
    """
    suite_id = await _seed_suite_via_service(db_session)

    resp = await app_client.post(f"/api/suites/{suite_id}/replay")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # Read back the inserted row via the same in-memory db_session.
    row = (
        await db_session.execute(select(RunRow).where(RunRow.id == run_id))
    ).scalar_one_or_none()
    assert row is not None, (
        f"replay-run row {run_id!r} not persisted after 202"
    )
    assert row.mode == "replay_run", (
        f"expected mode='replay_run', got {row.mode!r}"
    )
    assert row.status == "running", (
        f"expected status='running', got {row.status!r}"
    )
    assert row.suite_id == suite_id, (
        f"expected suite_id={suite_id!r}, got {row.suite_id!r}"
    )


# ---------------------------------------------------------------------------
# Test 9 — replay 404 (suite not found) / 409 (retired)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, expected_status, expected_detail",
    [
        pytest.param("missing", 404, "suite_not_found", id="suite_not_found"),
        pytest.param("retired", 409, "suite_retired", id="suite_retired"),
    ],
)
async def test_replay_suite_not_found_or_retired_combined(
    app_client: AsyncClient, db_session: AsyncSession,
    fixture: str, expected_status: int, expected_detail: str,
) -> None:
    """Replay precondition errors per spec §4 lines 343-344:

      * ``suite_not_found`` → 404 (unknown suite_id)
      * ``suite_retired``   → 409 (suite exists but ``retired_at`` is set)

    Both branches exercise the router's precondition path BEFORE the
    placeholder dispatch — no RunRow should be written when the response is
    a 4xx.
    """
    if fixture == "missing":
        suite_id = "nonexistent-suite-uuid"
    else:  # "retired"
        suite_id = await _seed_suite_via_service(db_session)
        # Manually mark the suite retired so the precondition trips.
        await db_session.execute(
            ValidationSuite.__table__.update()
            .where(ValidationSuite.id == suite_id)
            .values(
                retired_at=datetime.now(UTC).replace(tzinfo=None),
                retired_reason="test_setup",
            )
        )
        await db_session.commit()

    resp = await app_client.post(f"/api/suites/{suite_id}/replay")
    assert resp.status_code == expected_status, (
        f"expected {expected_status} {expected_detail}, got "
        f"{resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == expected_detail


# ---------------------------------------------------------------------------
# Test 10 — replay rate limit 5/min: 6th+ POST returns 429
# ---------------------------------------------------------------------------


async def test_replay_rate_limit_5_per_minute_returns_429(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec §4 line 276 — 5/min rate limit on ``POST /api/suites/{id}/replay``.

    Issuing 10 valid POSTs in quick succession against the SAME suite must
    trip the limit at the 6th — the first 5 are 202, the 6th+ are 429.
    """
    suite_id = await _seed_suite_via_service(db_session)

    statuses: list[int] = []
    for _ in range(10):
        resp = await app_client.post(f"/api/suites/{suite_id}/replay")
        statuses.append(resp.status_code)

    assert statuses[5] == 429, (
        f"6th replay must be 429 per the 5/min limit; got "
        f"{statuses[5]} (full sequence: {statuses})"
    )
    assert any(s == 202 for s in statuses[:5]), (
        f"no 202 in first 5 replays — limit assertion meaningless: "
        f"{statuses[:5]}"
    )


# ===========================================================================
# List / get / replays-list / retire (5 tests)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 11 — GET /api/suites: paginated envelope + ValidationSuiteListItem shape
# ---------------------------------------------------------------------------


async def test_get_suites_paginated_envelope_shape(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """``GET /api/suites`` returns a canonical paginated envelope per spec
    §4 line 273 — same shape as ``RunListResponse``:
    ``{total, count, offset, items, has_more, next_offset}``.

    Each ``items[*]`` is a :class:`ValidationSuiteListItem` (spec §4 lines
    417-424) — same as ``ValidationSuiteOut`` MINUS the heavy
    ``prompts_snapshot`` + ``baseline_scores`` JSON columns, plus the
    derived ``prompts_count`` and ``baseline_mean`` summary fields.
    """
    # Seed 3 distinct suites.
    for i in range(3):
        await _seed_suite_via_service(db_session, label=f"list-suite-{i}")

    resp = await app_client.get("/api/suites")
    assert resp.status_code == 200, (
        f"GET /api/suites must return 200, got "
        f"{resp.status_code}: {resp.text}"
    )

    body = resp.json()
    # Pagination envelope keys
    for key in ("total", "count", "offset", "items", "has_more", "next_offset"):
        assert key in body, f"missing pagination key {key!r}"
    assert body["total"] >= 3
    assert body["count"] == len(body["items"])
    assert body["offset"] == 0

    # First item shape — ValidationSuiteListItem fields per spec §4 lines 417-424.
    assert len(body["items"]) >= 1, "expected at least one suite in items"
    item = body["items"][0]
    for key in (
        "id", "source_run_id", "label", "tolerance_abs", "project_id",
        "repo_full_name", "created_at", "retired_at",
        "prompts_count", "baseline_mean",
    ):
        assert key in item, f"missing {key!r} in ValidationSuiteListItem"
    # ``prompts_snapshot`` MUST NOT leak into the list view (it's the heavy
    # column that ListItem deliberately excludes for paginated efficiency).
    assert "prompts_snapshot" not in item, (
        "ValidationSuiteListItem must exclude prompts_snapshot — "
        "spec §4 line 418 'Same minus JSON payloads'"
    )


# ---------------------------------------------------------------------------
# Test 12 — GET /api/suites/{id}: 404 on missing
# ---------------------------------------------------------------------------


async def test_get_suite_by_id_returns_404_on_missing(
    app_client: AsyncClient,
) -> None:
    """``GET /api/suites/{suite_id}`` on an unknown suite_id returns 404
    with ``detail='suite_not_found'`` per spec §4 line 343.
    """
    resp = await app_client.get("/api/suites/nonexistent-uuid-xyz")
    assert resp.status_code == 404, (
        f"unknown suite_id must yield 404 suite_not_found, got "
        f"{resp.status_code}: {resp.text}"
    )
    assert resp.json()["detail"] == "suite_not_found"


# ---------------------------------------------------------------------------
# Test 13 — GET /api/suites/{id}/replays: paginated RunSummary items
# ---------------------------------------------------------------------------


async def test_get_suite_replays_returns_run_summary_paginated(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """``GET /api/suites/{suite_id}/replays`` returns a canonical
    ``RunListResponse`` envelope (spec §4 line 275) — ``items`` is
    ``list[RunSummary]`` filtered to ``mode='replay_run' AND suite_id={id}``.

    The service primitive is already in place
    (``ValidationSuiteService.list_replays`` at validation_suite_service.py:890);
    this test exercises the router-level surface contract.
    """
    suite_id = await _seed_suite_via_service(db_session)

    # Insert 3 replay_run RunRows directly — bypass the replay endpoint to
    # avoid coupling this test to the placeholder dispatch flow.
    now = datetime.now(UTC).replace(tzinfo=None)
    for i in range(3):
        db_session.add(
            RunRow(
                id=f"replay-{i}",
                mode="replay_run",
                status="completed",
                started_at=now,
                completed_at=now,
                suite_id=suite_id,
                prompts_generated=3,
                aggregate={"mean_overall": 7.2 + i * 0.1},
            )
        )
    await db_session.commit()

    resp = await app_client.get(f"/api/suites/{suite_id}/replays")
    assert resp.status_code == 200, (
        f"GET /api/suites/{{id}}/replays must return 200, got "
        f"{resp.status_code}: {resp.text}"
    )

    body = resp.json()
    for key in ("total", "count", "offset", "items", "has_more", "next_offset"):
        assert key in body, f"missing pagination key {key!r}"
    assert body["total"] == 3

    # Items must all be replay_run rows scoped to this suite.
    assert len(body["items"]) == 3
    for item in body["items"]:
        # RunSummary core fields (schemas/runs.py:17-29).
        for key in ("id", "mode", "status", "started_at", "prompts_generated"):
            assert key in item, f"missing {key!r} in RunSummary"
        assert item["mode"] == "replay_run", (
            f"replay-list item leaked non-replay row: {item}"
        )


# ---------------------------------------------------------------------------
# Test 14 — POST /api/suites/{id}/retire: 200 + idempotent
# ---------------------------------------------------------------------------


async def test_retire_returns_200_idempotent(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """``POST /api/suites/{suite_id}/retire`` returns 200 on first call and
    again on second call (no error) — but the second call MUST NOT mutate
    ``retired_at`` (the timestamp is set on first retire only).

    Spec §4 line 277 + service §5 retire body docstring (lines 672-684):
    "Re-retire of an already-retired suite — second and subsequent calls
    short-circuit BEFORE WriteQueue.submit and return successfully. Three
    observability invariants hold on the no-op path: NO DB write, NO event
    publish, NO JSONL trace entry."

    The router-level invariant pinned here: response is 200 both times, and
    the persisted ``retired_at`` reads back identically (proves no second
    write fired).
    """
    suite_id = await _seed_suite_via_service(db_session)

    resp_first = await app_client.post(
        f"/api/suites/{suite_id}/retire",
        json={"reason": "first-retire"},
    )
    assert resp_first.status_code == 200, (
        f"first retire must return 200, got "
        f"{resp_first.status_code}: {resp_first.text}"
    )

    # Snapshot retired_at after first retire.
    row_after_first = (
        await db_session.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_id)
        )
    ).scalar_one()
    first_retired_at = row_after_first.retired_at
    assert first_retired_at is not None, (
        "retired_at must be set after first retire"
    )

    # Second retire — same suite, different reason. MUST be 200 and MUST
    # NOT mutate retired_at (idempotent no-op).
    resp_second = await app_client.post(
        f"/api/suites/{suite_id}/retire",
        json={"reason": "second-retire-attempt"},
    )
    assert resp_second.status_code == 200, (
        f"second retire must remain 200 (idempotent), got "
        f"{resp_second.status_code}: {resp_second.text}"
    )

    # Re-read — retired_at must be byte-identical.
    await db_session.commit()  # flush any state
    row_after_second = (
        await db_session.execute(
            select(ValidationSuite).where(ValidationSuite.id == suite_id)
        )
    ).scalar_one()
    assert row_after_second.retired_at == first_retired_at, (
        f"re-retire mutated retired_at: was {first_retired_at!r}, "
        f"now {row_after_second.retired_at!r}"
    )


# ---------------------------------------------------------------------------
# Test 15 — invalid retire reason: 400 + PATCH immutability assertion
# ---------------------------------------------------------------------------


async def test_retire_invalid_reason_returns_400(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Pydantic ``Field(min_length=1, max_length=500)`` on
    :class:`RetireSuiteRequest.reason` rejects empty and oversized reasons
    (spec §4 line 396).

    Additionally — scope-hardness pin per spec §14: NO ``PATCH /api/suites/{id}``
    route exists. Suites are immutable after creation; the ONLY state
    transition is the retire soft-delete writing ``retired_at`` /
    ``retired_reason``. A PATCH attempt MUST return 405 Method Not Allowed
    so the immutability invariant is wire-asserted.
    """
    suite_id = await _seed_suite_via_service(db_session)

    # Empty reason — too short
    resp_empty = await app_client.post(
        f"/api/suites/{suite_id}/retire",
        json={"reason": ""},
    )
    assert resp_empty.status_code == 400, (
        f"empty retire reason must yield 400, got "
        f"{resp_empty.status_code}: {resp_empty.text}"
    )

    # Reason too long — 600 chars exceeds the 500-char max
    resp_long = await app_client.post(
        f"/api/suites/{suite_id}/retire",
        json={"reason": "x" * 600},
    )
    assert resp_long.status_code == 400, (
        f"oversized retire reason must yield 400, got "
        f"{resp_long.status_code}: {resp_long.text}"
    )

    # Scope-hardness: PATCH /api/suites/{id} MUST be 405 (no route registered)
    # — pins the §14 immutability invariant at the routing layer.
    resp_patch = await app_client.patch(
        f"/api/suites/{suite_id}",
        json={"label": "renamed"},
    )
    assert resp_patch.status_code == 405, (
        f"PATCH /api/suites/{{id}} must be 405 Method Not Allowed "
        f"(suites immutable per spec §14), got "
        f"{resp_patch.status_code}: {resp_patch.text}"
    )
