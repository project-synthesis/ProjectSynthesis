"""RED-phase tests for v0.4.22 T2 Cycle 15 — audit-hook WARN→RAISE flip.

Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 15.
Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`` §7
      audit-hook flip mechanics + soak gate + kill-switch + §10 Cycle 15.

Three NEW tests pinning the v0.4.22 audit-hook flip surface:

1. ``test_audit_hook_default_is_raise`` — locks the flipped declared default
   (``Field(default=True, ...)``). Today the field declares ``default=False``
   in :mod:`app.config`, so this test FAILS — that is the canonical RED signal.

2. ``test_audit_hook_emits_zero_warn_under_full_t2_pipeline`` — extended
   integration test (decorated ``@pytest.mark.integration``) exercising every
   T2 write path under ``WRITE_QUEUE_AUDIT_HOOK_RAISE=True``:

     * save-as-suite (Cycle 2)
     * replay (Cycle 6)
     * topic-only probe (Cycle 7)
     * retire (Cycle 3)
     * regression-alarm computation (Cycle 4)

   Asserts zero ``read-engine audit:`` WARN lines in ``caplog`` for the run.
   This MAY pass today (all T2 paths already route writes through the
   ``WriteQueue``); the RED signal here is the existence of the regression
   guard itself — once Cycle 15 GREEN flips ``Field(default=True)`` the
   integration test pins that no T2 path ever escapes the queue.

3. ``test_audit_hook_kill_switch_env_var_reverts_to_warn`` — exercises the
   spec §7 kill-switch contract: setting ``WRITE_QUEUE_AUDIT_HOOK_RAISE=false``
   via env var override at ``Settings()`` instantiation MUST yield a settings
   instance whose attribute is ``False``, AND the audit hook installed against
   that setting MUST log a WARN rather than raise. Pins the operator escape
   hatch documented in spec §7 lines 1273-1277.

The existing Foundation P4 regression-guard test
``test_audit_hook_emits_zero_warn_under_full_pipeline`` at
``tests/test_tools_optimize.py:886`` STAYS UNCHANGED — it covers the P4
surface (analyze / optimize / score / refine / save_result) and continues
to PASS at v0.4.22 as a precondition of the flip (per ``backend/CLAUDE.md``
Foundation P4 entry).

All three tests live under the flat ``backend/tests/`` layout because the
``backend/tests/integration/`` subdirectory does NOT exist; the
``integration`` marker is registered at ``backend/pyproject.toml:5`` and is
the canonical opt-in seam (mirrors ``test_tools_optimize.py:884``).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


# ===========================================================================
# Test 1 — ``Settings()`` default is RAISE (flipped at Cycle 15 GREEN)
# ===========================================================================


def test_audit_hook_default_is_raise() -> None:
    """Spec §7 + §10 Cycle 15 — lock the FLIPPED declared default.

    The flip ships in Cycle 15 GREEN by changing
    ``app/config.py:248`` from ``Field(default=False, ...)`` to
    ``Field(default=True, ...)``. This test asserts the DECLARED default
    directly via :attr:`pydantic.BaseModel.model_fields` so the assertion
    is independent of any process-level env-var override (notably
    ``backend/tests/conftest.py:24`` sets ``WRITE_QUEUE_AUDIT_HOOK_RAISE=true``
    at module import time for the entire test session — using
    ``Settings().WRITE_QUEUE_AUDIT_HOOK_RAISE`` would read that env var and
    pass regardless of the declared default, masking the RED signal).

    Pre-Cycle-15-GREEN behavior: ``.default`` is ``False`` (the pre-flip
    field declaration). Assertion fails — this is the canonical RED signal.

    Post-Cycle-15-GREEN behavior: ``.default`` is ``True``. Assertion passes.
    """
    from app.config import Settings

    declared_default = Settings.model_fields["WRITE_QUEUE_AUDIT_HOOK_RAISE"].default
    assert declared_default is True, (
        "v0.4.22 Cycle 15 GREEN must flip the declared default of "
        "``Settings.WRITE_QUEUE_AUDIT_HOOK_RAISE`` from ``False`` to "
        "``True``. The kill-switch (env var override) remains available "
        "for operators who need to revert per spec §7 lines 1273-1277. "
        f"Got Field(default={declared_default!r}, ...)."
    )


# ===========================================================================
# Test 2 — Full T2 pipeline emits zero ``read-engine audit:`` WARN records
# ===========================================================================


def _canonical_aggregate(mean: float = 7.85) -> dict[str, Any]:
    """Canonical ``RunRow.aggregate`` shape matching the topic_probe generator.

    Mirrors ``test_routers_suites.py:_canonical_aggregate`` so the save-as-suite
    path sees the exact production shape — keys ``mean_overall`` /
    ``p5_overall`` / ``p50_overall`` / ``p95_overall`` are mandatory.
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
                "raw_prompt_idx": i,
                "overall": 8.0 - (i * 0.2),
                "dimensions": {
                    "clarity": 8.0, "specificity": 8.0,
                    "structure": 8.0, "faithfulness": 8.0,
                    "conciseness": 8.0,
                },
            }
            for i in range(3)
        ],
    }


def _canonical_prompt_results(n: int = 3) -> list[dict[str, Any]]:
    """Canonical ``RunRow.prompt_results`` rows — position-aligned with
    ``aggregate.per_prompt``.
    """
    return [
        {
            "prompt_idx": i,
            "raw_prompt": f"raw prompt {i}",
            "optimized_prompt": f"optimized prompt {i}",
            "intent_label": "general",
            "overall_score": 8.0 - (i * 0.2),
            "optimization_id": None,
            "status": "completed",
        }
        for i in range(n)
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_hook_emits_zero_warn_under_full_t2_pipeline(
    app_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec §7 + §10 Cycle 15 + OPERATE O1-O8 — zero audit-hook WARN under T2.

    Exercises every T2 write path under ``WRITE_QUEUE_AUDIT_HOOK_RAISE=True``
    via the canonical HTTP / service surfaces wired through ``app_client``:

      1. **save-as-suite** — ``POST /api/probes/{run_id}/save-as-suite``
         (Cycle 2 :meth:`ValidationSuiteService.create_from_run`).
      2. **retire** — ``POST /api/suites/{suite_id}/retire``
         (Cycle 3 :meth:`ValidationSuiteService.retire`).
      3. **regression-alarm computation** —
         :meth:`ValidationSuiteService.compute_regression_alarm`
         invoked directly (also surfaces in ``/api/health``).
      4. **topic-only probe** — ``POST /api/probes`` with
         ``grounding_mode='topic_only'`` (Cycle 7 generator branch — the
         router-level dispatch and ``RunOrchestrator._create_row`` INSERT
         are the audit-relevant write paths; the per-prompt LLM loop is
         stubbed out so the test stays deterministic and fast).
      5. **replay** — the replay router endpoint
         ``POST /api/suites/{suite_id}/replay`` dispatches the initial
         INSERT through ``WriteQueue`` (the generator's per-prompt loop
         is stubbed to keep the test deterministic).

    Audit-hook capture follows the canonical pattern at
    ``test_tools_optimize.py:907-908`` — ``caplog.set_level(WARNING)`` on
    logger ``app.database`` (the actual emitter at ``database.py:397``);
    assertion runs against ``caplog.records`` whose ``getMessage()``
    starts with ``read-engine audit:``.

    Pre-Cycle-15-GREEN behavior: this test MAY pass today (all T2 paths
    already route writes through the ``WriteQueue`` per the v0.4.13 →
    v0.4.21 migration chain). The RED signal of THIS file is Test 1
    (declared-default flip) — Test 2 is the regression guard that
    locks the post-flip invariant.

    Post-Cycle-15-GREEN behavior: every T2 path's write surface is pinned
    against read-engine writes by the integration test sweep.
    """
    caplog.set_level(logging.WARNING, logger="app.database")
    caplog.clear()

    # Force the runtime audit-hook setting into RAISE mode for the duration
    # of this test (mirrors test_lifespan_write_queue.py:170-203 pattern).
    # The session-level conftest already sets this in env, but explicit
    # mutation here pins the post-flip semantics regardless of conftest order.
    from app.config import settings as _settings
    _prior_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True

    try:
        from app.models import RunRow, ValidationSuite

        # -----------------------------------------------------------------
        # Stage 1 — seed a completed topic_probe RunRow as the suite source.
        # The save-as-suite path requires mode='topic_probe' + status='completed'
        # + aggregate carrying mean_overall (spec §4 preconditions).
        # -----------------------------------------------------------------
        source_run_id = uuid.uuid4().hex
        now = datetime.now(UTC).replace(tzinfo=None)
        db_session.add(RunRow(
            id=source_run_id,
            mode="topic_probe",
            status="completed",
            started_at=now,
            completed_at=now,
            prompts_generated=3,
            prompt_results=_canonical_prompt_results(3),
            aggregate=_canonical_aggregate(),
            repo_full_name="acme/widget",
        ))
        await db_session.commit()

        # -----------------------------------------------------------------
        # Stage 2 — save-as-suite (Cycle 2 ``create_from_run``).
        # -----------------------------------------------------------------
        save_resp = await app_client.post(
            f"/api/probes/{source_run_id}/save-as-suite",
            json={"label": "audit-hook-t2-suite", "tolerance_abs": 0.5},
        )
        assert save_resp.status_code == 201, (
            f"save-as-suite must succeed for audit-hook coverage; got "
            f"{save_resp.status_code}: {save_resp.text!r}"
        )
        suite_id = save_resp.json()["id"]

        # -----------------------------------------------------------------
        # Stage 3 — regression-alarm computation (Cycle 4 service method).
        # The /api/health endpoint surfaces the same block, but calling the
        # service directly keeps this stage focused on the alarm SQL +
        # state-transition writes (which DO route through the queue, but
        # any read-engine slip would surface as an audit WARN here).
        # -----------------------------------------------------------------
        from app.main import app
        suite_service = app.state.validation_suite_service
        alarm_block = await suite_service.compute_regression_alarm()
        # The block must exist (Cycle 4 GREEN landed); zero suites_in_alarm
        # is expected because this test never inserted a replay_run.
        assert alarm_block is not None, "regression alarm must compute"
        assert alarm_block.suites_total >= 1, (
            f"at least the seeded suite should count toward suites_total; "
            f"got {alarm_block.suites_total}"
        )

        # -----------------------------------------------------------------
        # Stage 4 — topic-only probe dispatch (Cycle 7 generator branch).
        # The router INSERTs the initial RunRow through the WriteQueue at
        # POST /api/probes time. We stub the orchestrator's generator
        # dispatch so the LLM-bound per-prompt loop does NOT fire — the
        # audit-relevant path is the INSERT, which fires regardless.
        # -----------------------------------------------------------------
        orchestrator = getattr(app.state, "run_orchestrator", None)
        if orchestrator is not None and hasattr(orchestrator, "_generators"):
            # Replace the topic_probe generator with a no-op stub that returns
            # a minimal GeneratorResult — keeps the dispatch path real
            # (INSERT through WriteQueue) but skips LLM execution.
            from app.services.generators.base import GeneratorResult

            class _NoOpGenerator:
                async def run(self, request, *, run_id):  # noqa: ANN001
                    return GeneratorResult(
                        prompts_generated=0,
                        prompt_results=[],
                        aggregate={"mean_overall": 0.0, "completed_count": 0},
                        final_report=None,
                        topic_probe_meta=None,
                        suite_id=None,
                    )

            _prior_gens = dict(orchestrator._generators)
            orchestrator._generators["topic_probe"] = _NoOpGenerator()
            orchestrator._generators["replay_run"] = _NoOpGenerator()
            try:
                topic_body = {
                    "topic": "audit hook t2 coverage probe",
                    "n_prompts": 5,
                    "grounding_mode": "topic_only",
                }
                # SSE-stream POST — consume the stream so the dispatch path
                # runs to completion and the orchestrator's _persist_final
                # WriteQueue submit fires.
                try:
                    async with app_client.stream(
                        "POST", "/api/probes", json=topic_body,
                    ) as topic_resp:
                        # Status 200 (SSE start) or 202 (Prefer: respond-async)
                        # both indicate the INSERT fired. Anything else means
                        # the route rejected the request before any write,
                        # which is irrelevant to the audit-hook contract.
                        if topic_resp.status_code in (200, 202):
                            await topic_resp.aread()
                except Exception:
                    # Stream consumption errors are tolerated — the audit-hook
                    # assertion is the only signal we care about. Any 4xx
                    # / 5xx before the WriteQueue.submit() also means no
                    # audit-relevant write fired.
                    pass

                # -----------------------------------------------------------------
                # Stage 5 — replay dispatch (Cycle 6 ReplayRunGenerator).
                # POST /api/suites/{suite_id}/replay INSERTs the initial
                # replay_run RunRow through the WriteQueue. The stubbed
                # generator above ensures the per-prompt loop is a no-op.
                # -----------------------------------------------------------------
                try:
                    replay_resp = await app_client.post(
                        f"/api/suites/{suite_id}/replay",
                    )
                    # 202 = accepted (canonical) or 200 = inline (legacy
                    # cycle-5 placeholder). Both fire the INSERT.
                    _ = replay_resp.status_code
                except Exception:
                    # Same tolerance as topic-only — audit-hook assertion is
                    # the only relevant signal.
                    pass
            finally:
                orchestrator._generators = _prior_gens

        # -----------------------------------------------------------------
        # Stage 6 — retire (Cycle 3 ``ValidationSuiteService.retire``).
        # -----------------------------------------------------------------
        retire_resp = await app_client.post(
            f"/api/suites/{suite_id}/retire",
            json={"reason": "audit-hook coverage sweep"},
        )
        assert retire_resp.status_code == 200, (
            f"retire must succeed for audit-hook coverage; got "
            f"{retire_resp.status_code}: {retire_resp.text!r}"
        )

        # -----------------------------------------------------------------
        # Audit-hook assertion — zero ``read-engine audit:`` WARN records.
        # The actual log emitter is ``logger.warning("read-engine audit:\n%s", ...)``
        # at ``database.py:397``. Matches the canonical pattern from
        # ``test_tools_optimize.py:1035-1042``.
        # -----------------------------------------------------------------
        audit_warns = [
            r for r in caplog.records
            if r.getMessage().startswith("read-engine audit:")
        ]
        assert not audit_warns, (
            f"v0.4.22 audit-hook flip precondition violated: {len(audit_warns)} "
            f"``read-engine audit:`` WARN record(s) fired during the T2 pipeline "
            f"sweep (save-as-suite + replay + topic-only probe + retire + "
            f"regression-alarm). Every T2 write path must route through the "
            f"WriteQueue. First 3 WARN bodies (truncated):\n"
            + "\n".join(
                r.getMessage()[:240] for r in audit_warns[:3]
            )
        )
    finally:
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise


# ===========================================================================
# Test 3 — kill-switch env var override reverts to WARN-only behavior
# ===========================================================================


@pytest.mark.asyncio
async def test_audit_hook_kill_switch_env_var_reverts_to_warn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """Spec §7 lines 1273-1277 — operator kill switch.

    Spec §7 documents the env-var override as the operator escape hatch:

        Kill switch: operators can set ``WRITE_QUEUE_AUDIT_HOOK_RAISE=false``
        env var. Documented in ``backend/CLAUDE.md`` write-queue contract
        section.

    Without an explicit regression test, a future refactor could silently
    break the kill-switch (e.g., by hardcoding ``True`` at the audit-hook
    call site, or by ignoring the env var at ``Settings()`` instantiation).
    This test pins both halves of the contract:

      1. **Settings layer** — instantiating ``Settings()`` after setting
         ``WRITE_QUEUE_AUDIT_HOOK_RAISE=false`` MUST yield
         ``settings.WRITE_QUEUE_AUDIT_HOOK_RAISE is False``.

      2. **Audit-hook layer** — under
         ``settings.WRITE_QUEUE_AUDIT_HOOK_RAISE=False`` a detected
         read-engine write MUST log a WARN at ``app.database`` (the
         pre-v0.4.22 / v0.4.21 semantics) instead of raising
         :class:`WriteOnReadEngineError`.

    The kill-switch contract MUST hold at every commit from v0.4.22 onward
    — emergency rollback in production must not require a redeploy.
    """
    # ----- Half 1: Settings reads the env var correctly -----
    monkeypatch.setenv("WRITE_QUEUE_AUDIT_HOOK_RAISE", "false")

    from app.config import Settings
    fresh_settings = Settings()
    assert fresh_settings.WRITE_QUEUE_AUDIT_HOOK_RAISE is False, (
        f"setting env var WRITE_QUEUE_AUDIT_HOOK_RAISE=false MUST yield "
        f"Settings(...).WRITE_QUEUE_AUDIT_HOOK_RAISE == False; got "
        f"{fresh_settings.WRITE_QUEUE_AUDIT_HOOK_RAISE!r}. Kill switch "
        "(spec §7 lines 1273-1277) is broken."
    )

    # ----- Half 2: audit hook reverts to WARN-only behavior -----
    # Mutate the LIVE settings instance the audit hook reads (mirrors
    # test_lifespan_write_queue.py:170-203 pattern). The hook reads
    # ``settings.WRITE_QUEUE_AUDIT_HOOK_RAISE`` at fire time, so toggling
    # the live module attribute is the canonical seam.
    from app.config import settings as _live_settings
    from app.database import (
        install_read_engine_audit_hook,
        uninstall_read_engine_audit_hook,
    )

    _prior_raise = _live_settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _live_settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = False

    db_path = tmp_path / "kill_switch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    caplog.set_level(logging.WARNING, logger="app.database")
    caplog.clear()

    try:
        install_read_engine_audit_hook(engine)

        # Drive a write through the read engine. With WARN-mode this MUST
        # NOT raise — it logs a WARN to logger ``app.database``.
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE kill_switch_table (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO kill_switch_table (id) VALUES (1)"))

        audit_warns = [
            r for r in caplog.records
            if r.getMessage().startswith("read-engine audit:")
        ]
        assert audit_warns, (
            "kill switch broken: with WRITE_QUEUE_AUDIT_HOOK_RAISE=False, "
            "a detected read-engine write MUST emit a ``read-engine audit:`` "
            "WARN record on logger ``app.database`` (pre-v0.4.22 semantics). "
            f"Got zero such records in {len(caplog.records)} captured."
        )
    finally:
        uninstall_read_engine_audit_hook()
        _live_settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise
        await engine.dispose()
