"""RED-phase tests for ``/api/health`` ``regression_alarm`` block — T2 Cycle 9 (5 tests).

Plan:  ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 9 Task 9.1
Spec:  ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
       § 3 (``regression_alarm`` JSON block shape) + § 10 Cycle 9

These tests pin the health endpoint's ``regression_alarm`` block contract:

* Top-level ``regression_alarm`` key always present (never null / never missing).
* Empty-fleet shape: ``{suites_total: 0, suites_in_alarm: 0, latest_alarms: []}``.
* Nominal-fleet shape: ``suites_in_alarm == 0`` when all replays are within
  tolerance, no entries in ``latest_alarms``.
* Firing shape: each firing entry carries the seven canonical
  ``RegressionAlarmEntry`` columns (suite_id, label, baseline_mean,
  latest_mean, delta_abs, tolerance_abs, latest_replay_id, latest_replay_at).
* 30s TTL cache: two consecutive ``GET /api/health`` calls run the alarm
  JOIN exactly once — Cycle 4 already pins this on
  ``ValidationSuiteService.compute_regression_alarm`` at the service layer;
  this test pins the same invariant through the HTTP surface.
* Wire shape validates as ``RegressionAlarmBlock`` Pydantic model.

Pre-condition before Cycle 9 GREEN lands: ``app/routers/health.py`` does NOT
include any ``regression_alarm`` rendering — every assertion below trips on
``KeyError("regression_alarm")`` / ``assert "regression_alarm" in body``.

Cycle 4 already shipped ``ValidationSuiteService.compute_regression_alarm``
plus the supporting Pydantic models. Cycle 9 GREEN wires the service into
``health.py`` so the block appears in the HTTP response.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow, ValidationSuite
from app.schemas.validation_suite import RegressionAlarmBlock

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Local seeding helpers — direct ORM inserts against ``db_session`` so the
# tests do not depend on the full ``ValidationSuiteService.create_from_run``
# happy-path (already covered by Cycle 2 tests). These tests need only the
# alarm SQL JOIN to see ``ValidationSuite`` + ``RunRow`` rows.
# ---------------------------------------------------------------------------


_DEFAULT_BASELINE_MEAN: float = 7.85
_DEFAULT_TOLERANCE: float = 0.5


def _baseline_scores(mean: float = _DEFAULT_BASELINE_MEAN) -> dict:
    """Canonical ``baseline_scores`` shape matching ``BaselineScoresPayload``.

    Mirrors ``tests/test_validation_suite_service.py:_canonical_aggregate`` for
    the keys the alarm path actually reads (``mean_overall`` is the only one
    the Python-side filter consumes).
    """
    return {
        "mean_overall": mean,
        "p5_overall": mean - 1.65,
        "p50_overall": mean,
        "p95_overall": mean + 1.25,
        "per_prompt": [],
        "task_type_distribution": {"coding": 1},
    }


async def _seed_suite(
    db: AsyncSession,
    *,
    label: str,
    baseline_mean: float = _DEFAULT_BASELINE_MEAN,
    tolerance_abs: float = _DEFAULT_TOLERANCE,
) -> ValidationSuite:
    """Insert a ``ValidationSuite`` row and return the persisted instance.

    Uses direct ORM insertion against the shared ``db_session`` — the
    ``ValidationSuiteService.create_from_run`` happy-path is exercised in
    Cycle 2 tests; Cycle 9 only needs the alarm SQL to see rows in the table.
    """
    suite = ValidationSuite(
        id=uuid.uuid4().hex,
        source_run_id=None,
        prompts_snapshot=[],
        baseline_scores=_baseline_scores(baseline_mean),
        tolerance_abs=tolerance_abs,
        label=label,
        project_id=None,
        repo_full_name=None,
        created_at=datetime.now(UTC),
        retired_at=None,
        retired_reason=None,
    )
    db.add(suite)
    await db.commit()
    return suite


async def _seed_replay(
    db: AsyncSession,
    *,
    suite_id: str,
    mean_overall: float,
    started_at: datetime | None = None,
) -> RunRow:
    """Insert a ``RunRow(mode='replay_run', status='completed', suite_id=...)``.

    The alarm SQL JOIN reads ``replay_aggregate['mean_overall']`` —
    other aggregate keys are not consumed, so the minimal payload suffices.
    """
    started = started_at or datetime.now(UTC)
    row = RunRow(
        id=uuid.uuid4().hex,
        mode="replay_run",
        status="completed",
        started_at=started,
        completed_at=started,
        prompts_generated=0,
        prompt_results=None,
        aggregate={"mean_overall": mean_overall},
        suite_id=suite_id,
    )
    db.add(row)
    await db.commit()
    return row


# ===========================================================================
# Test 1 — empty block when no suites exist
# ===========================================================================


async def test_health_regression_alarm_block_empty_when_no_suites(
    app_client: AsyncClient,
) -> None:
    """Spec § 3 — ``regression_alarm`` is rendered on every ``/api/health``
    response with the canonical 3-field shape, even when the
    ``validation_suite`` table is empty.

    Empty-fleet contract:
      * ``suites_total == 0`` — no active suites.
      * ``suites_in_alarm == 0`` — no replays to evaluate.
      * ``latest_alarms == []`` — empty list, NOT null.

    The block MUST be present (never elided) so frontend consumers can render
    the panel in a "no suites yet" empty state without null-handling.
    """
    resp = await app_client.get("/api/health?probes=false")

    assert resp.status_code == 200, (
        f"expected 200 from /api/health on empty-suites fixture; got "
        f"{resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assert "regression_alarm" in body, (
        f"/api/health response MUST include the 'regression_alarm' top-level "
        f"key per spec § 3 — even when no suites exist. Available top-level "
        f"keys: {sorted(body.keys())!r}"
    )

    block = body["regression_alarm"]
    assert block.get("suites_total") == 0, (
        f"empty-suites fixture must report suites_total=0; got "
        f"{block.get('suites_total')!r}"
    )
    assert block.get("suites_in_alarm") == 0, (
        f"empty-suites fixture must report suites_in_alarm=0; got "
        f"{block.get('suites_in_alarm')!r}"
    )
    assert block.get("latest_alarms") == [], (
        f"empty-suites fixture must report latest_alarms=[] (empty list, "
        f"not null); got {block.get('latest_alarms')!r}"
    )


# ===========================================================================
# Test 2 — nominal-fleet block with all replays within tolerance
# ===========================================================================


async def test_health_regression_alarm_block_nominal_with_replays(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec § 3 + § 5 — when every replay is within ``tolerance_abs`` of its
    suite's baseline_mean, ``suites_in_alarm == 0`` and ``latest_alarms`` is
    empty even though ``suites_total > 0``.

    Setup: 3 suites with baseline_mean=7.85, tolerance=0.5; each gets a
    nominal replay (mean_overall=7.70 → delta=-0.15, within tolerance).

    Spec § 5 Python-side filter: ``latest_mean < baseline_mean - tolerance_abs``
    is FALSE for 7.70 < 7.85 - 0.5 == 7.35 → suite is nominal.
    """
    suites = []
    for i in range(3):
        suite = await _seed_suite(db_session, label=f"nominal-suite-{i}")
        suites.append(suite)
        # Within-tolerance replay (delta=-0.15 < tolerance=0.5 → nominal).
        await _seed_replay(
            db_session, suite_id=suite.id, mean_overall=7.70,
        )

    resp = await app_client.get("/api/health?probes=false")
    assert resp.status_code == 200, (
        f"expected 200; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assert "regression_alarm" in body, (
        f"/api/health MUST include 'regression_alarm' even on nominal-fleet "
        f"fixtures; top-level keys: {sorted(body.keys())!r}"
    )
    block = body["regression_alarm"]

    assert block.get("suites_total") == 3, (
        f"nominal-fleet fixture seeded 3 active suites; suites_total must "
        f"reflect ALL active suites (not just firing ones); got "
        f"{block.get('suites_total')!r}"
    )
    assert block.get("suites_in_alarm") == 0, (
        f"nominal-fleet fixture has every replay within tolerance — "
        f"suites_in_alarm must be 0; got {block.get('suites_in_alarm')!r}"
    )
    assert block.get("latest_alarms") == [], (
        f"nominal-fleet fixture must emit latest_alarms=[]; got "
        f"{block.get('latest_alarms')!r}"
    )


# ===========================================================================
# Test 3 — firing-fleet block surfaces the regressed suite in latest_alarms
# ===========================================================================


async def test_health_regression_alarm_block_firing_includes_in_latest_alarms(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec § 5 — when a replay's ``mean_overall`` falls below
    ``baseline_mean - tolerance_abs``, the alarm fires and the suite appears
    in ``latest_alarms`` with the canonical seven-column entry shape.

    Setup: baseline=7.85, tolerance=0.5, latest replay mean=7.21
        → 7.21 < 7.85 - 0.5 == 7.35 (TRUE)
        → fires with delta_abs = 7.21 - 7.85 = -0.64 (regression-direction).

    Spec § 4 lines 444-449 enumerate every required entry column:
        suite_id, label, baseline_mean, latest_mean, delta_abs,
        tolerance_abs, latest_replay_id, latest_replay_at.
    """
    suite = await _seed_suite(
        db_session,
        label="firing-suite",
        baseline_mean=7.85,
        tolerance_abs=0.5,
    )
    replay = await _seed_replay(
        db_session, suite_id=suite.id, mean_overall=7.21,
    )

    resp = await app_client.get("/api/health?probes=false")
    assert resp.status_code == 200, (
        f"expected 200; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assert "regression_alarm" in body, (
        f"/api/health MUST include 'regression_alarm' on firing-fleet "
        f"fixtures; top-level keys: {sorted(body.keys())!r}"
    )
    block = body["regression_alarm"]

    assert block.get("suites_total") == 1, (
        f"firing-fleet fixture seeded 1 active suite; got "
        f"{block.get('suites_total')!r}"
    )
    assert block.get("suites_in_alarm") == 1, (
        f"firing replay (delta=-0.64, tolerance=0.5) must fire — "
        f"suites_in_alarm must be 1; got {block.get('suites_in_alarm')!r}"
    )

    alarms = block.get("latest_alarms") or []
    assert len(alarms) == 1, (
        f"firing-fleet fixture must produce exactly 1 entry in latest_alarms; "
        f"got {len(alarms)}: {alarms!r}"
    )

    entry = alarms[0]
    assert entry.get("suite_id") == suite.id, (
        f"alarm entry suite_id must match seeded suite; expected={suite.id!r}, "
        f"got={entry.get('suite_id')!r}"
    )
    assert entry.get("label") == "firing-suite", (
        f"alarm entry label must round-trip from ValidationSuite.label; got "
        f"{entry.get('label')!r}"
    )
    assert entry.get("baseline_mean") == pytest.approx(7.85), (
        f"baseline_mean must equal the suite's baseline_scores.mean_overall "
        f"(7.85); got {entry.get('baseline_mean')!r}"
    )
    assert entry.get("latest_mean") == pytest.approx(7.21), (
        f"latest_mean must equal replay aggregate.mean_overall (7.21); got "
        f"{entry.get('latest_mean')!r}"
    )
    # delta_abs = latest_mean - baseline_mean = 7.21 - 7.85 = -0.64
    # (regression-direction filter: negative on firing entries).
    assert entry.get("delta_abs") == pytest.approx(-0.64, abs=1e-6), (
        f"delta_abs must equal (latest_mean - baseline_mean) = -0.64 within "
        f"float epsilon; got {entry.get('delta_abs')!r}"
    )
    assert entry.get("tolerance_abs") == pytest.approx(0.5), (
        f"tolerance_abs must round-trip from suite column; got "
        f"{entry.get('tolerance_abs')!r}"
    )
    assert entry.get("latest_replay_id") == replay.id, (
        f"latest_replay_id must equal the MAX(started_at) RunRow.id; "
        f"expected={replay.id!r}, got={entry.get('latest_replay_id')!r}"
    )
    # latest_replay_at is serialized to ISO 8601 over the wire — must be
    # present and non-empty (round-trip into datetime would require the
    # canonical RunRow timestamp; the wire-shape contract is that the field
    # is a non-empty string with date-marker characters).
    latest_replay_at = entry.get("latest_replay_at")
    assert isinstance(latest_replay_at, str) and len(latest_replay_at) > 0, (
        f"latest_replay_at must be a non-empty ISO 8601 string on the wire; "
        f"got {latest_replay_at!r}"
    )


# ===========================================================================
# Test 4 — 30s TTL cache: two consecutive /api/health calls run alarm SQL once
# ===========================================================================


async def test_health_regression_alarm_30s_ttl_cache_respected(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec § 3 — ``regression_alarm`` block backed by a 30s ``TTLCache``
    (matches ``taxonomy/sub_domain_readiness.py`` pattern). Two
    ``GET /api/health`` calls within the TTL window MUST execute the alarm
    JOIN exactly once — the second call short-circuits on the cached
    ``RegressionAlarmBlock``.

    Measurement: count alarm-SQL invocations through the conftest's patched
    ``async_session_factory`` by wrapping each returned session's
    ``.execute`` method. The alarm SQL is the only statement that references
    BOTH ``validation_suite`` and ``run_row`` table names (incidental
    single-table reads from other ``/api/health`` paths cannot match).

    Cycle 4 already pins this invariant on
    ``ValidationSuiteService.compute_regression_alarm`` (Test 26 in
    ``test_validation_suite_service.py``); Cycle 9 GREEN MUST preserve the
    behavior through the HTTP surface — i.e., the route must not instantiate
    a fresh service instance per request (which would defeat the
    instance-scoped TTL cache).
    """
    # Seed a firing condition so the alarm path produces non-trivial work
    # (the JOIN result row count drives the Python-side filter; without any
    # replays the JOIN returns zero rows and the cache test still measures
    # the JOIN dispatch but not the post-fetch path).
    suite = await _seed_suite(
        db_session,
        label="ttl-fixture",
        baseline_mean=7.85,
        tolerance_abs=0.5,
    )
    await _seed_replay(db_session, suite_id=suite.id, mean_overall=7.21)

    # Wrap async_session_factory so every session opened during /api/health
    # records its ``.execute`` invocations. Only statements that JOIN
    # validation_suite with run_row count as alarm-SQL — the other health
    # queries (PromptCluster, GlobalPattern, Optimization) touch one table
    # at a time so cannot match.
    import app.database as database_mod
    real_factory = database_mod.async_session_factory
    sql_execute_count = {"alarm_sql": 0}

    def _wrap_factory(*args, **kwargs):
        ctx = real_factory(*args, **kwargs)
        real_aenter = ctx.__aenter__

        async def _aenter_wrapper(*a, **k):
            session = await real_aenter(*a, **k)
            real_execute = session.execute

            async def _counting_execute(stmt, *e_args, **e_kwargs):
                try:
                    rendered = str(stmt)
                except (TypeError, ValueError):
                    rendered = ""
                # Alarm SQL JOINs validation_suite with run_row — both literal
                # table names appear in the rendered FROM clause. Mirrors the
                # signature used by Cycle 4 Test 26.
                if "validation_suite" in rendered and "run_row" in rendered:
                    sql_execute_count["alarm_sql"] += 1
                return await real_execute(stmt, *e_args, **e_kwargs)

            session.execute = _counting_execute  # type: ignore[method-assign]
            return session

        ctx.__aenter__ = _aenter_wrapper
        return ctx

    @contextlib.contextmanager
    def _patched():
        original = database_mod.async_session_factory
        database_mod.async_session_factory = _wrap_factory  # type: ignore[assignment]
        try:
            yield
        finally:
            database_mod.async_session_factory = original

    with _patched():
        # First call — cache miss, MUST execute the alarm JOIN at least once.
        resp_1 = await app_client.get("/api/health?probes=false")
        assert resp_1.status_code == 200, (
            f"first /api/health call failed: {resp_1.status_code}: {resp_1.text}"
        )
        first_call_count = sql_execute_count["alarm_sql"]
        assert first_call_count >= 1, (
            f"first /api/health call MUST execute the regression-alarm "
            f"JOIN at least once (cache miss); got "
            f"alarm_sql_executions={first_call_count}"
        )

        # Second call within TTL — cache HIT, MUST NOT re-execute the JOIN.
        resp_2 = await app_client.get("/api/health?probes=false")
        assert resp_2.status_code == 200, (
            f"second /api/health call failed: "
            f"{resp_2.status_code}: {resp_2.text}"
        )
        second_call_count = sql_execute_count["alarm_sql"]
        assert second_call_count == first_call_count, (
            f"30s TTL cache violated — second /api/health call within window "
            f"re-executed the alarm JOIN. Expected total executions to stay "
            f"at {first_call_count}; got {second_call_count}. The cache "
            f"must short-circuit the second call before opening a read "
            f"session. (Service-layer contract: "
            f"``ValidationSuiteService.compute_regression_alarm`` 30s TTL "
            f"per spec § 5; HTTP-layer contract: route MUST reuse the "
            f"service instance across requests.)"
        )

    # Sanity: both responses include the block, and the block shape matches.
    body_1 = resp_1.json()
    body_2 = resp_2.json()
    assert "regression_alarm" in body_1 and "regression_alarm" in body_2, (
        f"both /api/health responses must include 'regression_alarm'; "
        f"call1 keys={sorted(body_1.keys())!r}, "
        f"call2 keys={sorted(body_2.keys())!r}"
    )
    block_1 = body_1["regression_alarm"]
    block_2 = body_2["regression_alarm"]
    assert block_1.get("suites_total") == block_2.get("suites_total")
    assert block_1.get("suites_in_alarm") == block_2.get("suites_in_alarm")


# ===========================================================================
# Test 5 — wire shape validates against the RegressionAlarmBlock Pydantic model
# ===========================================================================


async def test_health_regression_alarm_uses_existing_response_shape(
    app_client: AsyncClient, db_session: AsyncSession,
) -> None:
    """Spec § 4 — the wire shape of the ``regression_alarm`` block matches the
    canonical :class:`RegressionAlarmBlock` Pydantic schema declared in
    ``app/schemas/validation_suite.py``. This pins the GREEN path to
    *consume* the existing schema (no parallel dict-only response) and
    surfaces any drift between health.py's serialization and the contract
    every other consumer (MCP tools, frontend types) reads from.

    Setup: seed a firing condition so the full ``RegressionAlarmEntry`` shape
    (with non-empty ``latest_alarms``) round-trips through the validator.
    """
    suite = await _seed_suite(
        db_session,
        label="shape-fixture",
        baseline_mean=7.85,
        tolerance_abs=0.5,
    )
    await _seed_replay(db_session, suite_id=suite.id, mean_overall=7.10)

    resp = await app_client.get("/api/health?probes=false")
    assert resp.status_code == 200, (
        f"expected 200; got {resp.status_code}: {resp.text}"
    )
    body = resp.json()

    assert "regression_alarm" in body, (
        f"/api/health response MUST include 'regression_alarm'; top-level "
        f"keys: {sorted(body.keys())!r}"
    )
    block_raw = body["regression_alarm"]

    # The wire shape must validate cleanly against RegressionAlarmBlock —
    # any missing / mistyped column raises ValidationError here.
    try:
        block = RegressionAlarmBlock.model_validate(block_raw)
    except Exception as exc:  # noqa: BLE001 — surface the validation error in the assert
        pytest.fail(
            f"regression_alarm block does NOT match RegressionAlarmBlock "
            f"Pydantic shape — schema drift between health.py serialization "
            f"and schemas/validation_suite.py. Wire payload: {block_raw!r}. "
            f"Validation error: {exc!r}"
        )

    # Cross-check the validated model exposes the canonical field set.
    assert block.suites_total == 1
    assert block.suites_in_alarm == 1
    assert len(block.latest_alarms) == 1

    # Every RegressionAlarmEntry field must round-trip (matches Cycle 4
    # Test 24 column-by-column contract — none silently dropped).
    entry = block.latest_alarms[0]
    assert entry.suite_id == suite.id
    assert entry.label == "shape-fixture"
    assert entry.baseline_mean == pytest.approx(7.85)
    assert entry.latest_mean == pytest.approx(7.10)
    assert entry.delta_abs == pytest.approx(-0.75, abs=1e-6)
    assert entry.tolerance_abs == pytest.approx(0.5)
    assert isinstance(entry.latest_replay_id, str) and entry.latest_replay_id
    assert entry.latest_replay_at is not None
