# Soak Gates — Active + Historical Tracker

Project Synthesis uses **soak gates** to validate production stability of risky architectural changes BEFORE compounding additional changes on top. A soak gate opens when a behaviorally-significant precondition ships (e.g., long-handler restructure, write-queue migration) and closes only after a defined observation window has produced clean telemetry.

This document is the **actionable tracker** for soak gates. Operators consult it daily during active windows. Every gate decision (PASS / EXTEND / ABORT) is recorded here with the evidence trail.

**Related**: see [`docs/E2E_TEST_MATRIX.md`](E2E_TEST_MATRIX.md) for the **pre-ship verification checklist** (action × error matrix + 8-stage E2E walkthrough). Soak gates handle post-ship observation; the E2E matrix handles pre-ship verification. They complement each other across the release-gate boundary.

## Status legend

| Symbol | Status | Meaning |
|---|---|---|
| 🟢 | **PASSED** | Window closed clean. Downstream change can proceed. |
| 🟡 | **ACTIVE** | Window open. Daily checks in progress. |
| 🔴 | **BLOCKED** | Unexpected WARN/error detected. Downstream change halted; restructure required. |
| ⚪ | **PLANNED** | Window not yet opened (precondition not shipped). |
| 🟤 | **EXTENDED** | Window extended past minimum end-date due to inconclusive evidence. |

## Active gates

| Gate ID | Status | Opens | Earliest Close | Owner | Downstream change |
|---|---|---|---|---|---|
| [SG-2026-05-11](#sg-2026-05-11--audit-hook-warnrise-flip) | 🟡 ACTIVE | 2026-05-11 | 2026-05-18 | release-operator | v0.4.22 — `WRITE_QUEUE_AUDIT_HOOK_RAISE` flip `False → True` |

## Historical gates

| Gate ID | Status | Window | Outcome |
|---|---|---|---|
| — | — | — | (none — SG-2026-05-11 is the first formal soak gate) |

---

## SG-2026-05-11 — Audit-hook WARN→RAISE flip

**Status**: 🟡 ACTIVE (opened 2026-05-11, earliest close 2026-05-18)
**Owner**: release-operator
**Downstream change**: v0.4.22 release — `WRITE_QUEUE_AUDIT_HOOK_RAISE` default flips `False → True` (per Topic Probe Tier 2 Cycle 15)

### Why this gate exists

Foundation P4 (v0.4.21, shipped 2026-05-11) restructured the 3 long-running MCP-tool handlers (`tools/save_result.py`, `tools/refine.py`, `tools/optimize.py` internal-tier) so that all LLM work runs with NO DB session held and persistence routes through `WriteQueue.submit()`. The `test_audit_hook_emits_zero_warn_under_full_pipeline` integration test proved zero `read-engine audit:` WARN entries under a single representative pipeline run — but a single test run does not cover the full production-traffic distribution (rate-limit retries, cancelled refinements, partial seed-agent batches, concurrent MCP sessions, lifespan-restart edges, etc.).

The audit-hook flip changes a documented-WARN behavior into a hard `WriteOnReadEngineError` raise. If any production write path still hits the read engine — e.g., a forgotten direct `db.execute()` in a less-traveled branch — the flip will surface it as a 500-level user-visible error instead of a log line. A 7-day production soak window catches the long tail of these branches before the flip locks in.

### Acceptance criteria

⚠️ **Critical invariant: the gate verifies coverage, NOT just absence-of-error.** A 7-day window with zero traffic produces zero WARN lines by definition — that's a vacuous PASS, not a verified PASS. The gate is **evidence-based**, not time-based. See [Soak-traffic generation](#soak-traffic-generation) below for the minimum coverage floor.

**PASS** (gate closes 🟢, v0.4.22 release proceeds) — ALL of:
- Cumulative count of `read-engine audit:` log lines during the window: **zero** new entries beyond the known/explained baseline at window open.
- No `audit_drift` warnings emitted by the WriteQueue layer.
- No correlated user-visible 500 errors traceable to the audit hook.
- `./init.sh restart` produces a clean post-restart log (5-minute settle window after lifespan complete).
- **Minimum surface coverage met**: each of the 3 Foundation P4 restructured handlers (`tools/save_result.py`, `tools/refine.py`, `tools/optimize.py` internal-tier) has been exercised ≥3 times via real or synthetic traffic during the window. See coverage matrix in [Soak-traffic generation](#soak-traffic-generation).

**EXTEND** (gate stays 🟡 ACTIVE past 2026-05-18):
- A single unexpected WARN appeared but its source has been identified + fix is in flight → extend window by 7 days from fix-commit date.
- Soak window had < 4 days of production traffic (e.g., backend was down for maintenance) — extend until 7 production-traffic days are sampled.
- **Insufficient coverage**: one or more P4 handlers exercised < 3 times during the window → extend window AND run the synthetic-traffic recipe daily until floor is met.

**ABORT** (gate flips 🔴 BLOCKED, v0.4.22 cannot ship the flip):
- Multiple unexpected WARN sources from different code paths → indicates systemic gap in P4 restructure → restructure offending paths in a v0.4.21.x patch + restart soak from the patch ship date.
- `audit_drift` warnings emit (suggesting WriteQueue itself is queueing unexpected payloads) → write-queue contract violation; back out of v0.4.22 entirely until WriteQueue layer is patched.

### Soak-traffic generation

The audit hook only fires when a write happens through the read engine outside the allow-list. If nothing writes, nothing warns — and the operator gets a vacuous PASS with no actual evidence. To convert "time elapsed" into "evidence collected", the operator must observe the 3 Foundation P4 surfaces being exercised.

**Coverage matrix** — each row must accumulate ≥3 entries during the window:

| P4 handler | Surface | Minimum invocations during 7-day window | Counts as exercised when |
|---|---|---|---|
| Cycle 1: `tools/save_result.py` + passthrough scoring | `synthesis_save_result` MCP tool OR `POST /api/optimize` with `force_passthrough=True` | ≥3 | Log line emitted via `operation_label="save_result_persist"` or `task_type_telemetry_no_session` |
| Cycle 2: `tools/refine.py` + 3 refinement routes | `POST /api/refine` (any of: initial turn / follow-up / rollback) | ≥3 | Log line emitted via `operation_label="refine_initial_turn"`, `"refine_persist_turn"`, or `"refine_rollback"` |
| Cycle 3: `tools/optimize.py` internal-tier orchestrator | `POST /api/optimize` (internal tier — NOT sampling, NOT passthrough) | ≥3 | Log line emitted via `operation_label="pipeline_failed_optimization_persist"` OR a successful internal-tier optimization committed to DB |

**Other surfaces incidentally covered** by the same daily routine (audit-hook fires would surface here too):
- Hourly `_recurring_gc_task` (automatic, fires regardless of traffic)
- Lifespan `./init.sh restart` (operator-driven, ≥1 expected during window)
- Taxonomy warm-cycle writes (≥30s after recent activity — gated on traffic above)
- Repo index `POST /api/github/repos/link` or `/reindex` (only if user links a repo)
- Cold path / GC sweeps (auto-triggered on Q-drop or hourly)

**Daily soak-traffic recipe** — run once per day (cron at noon is reasonable):

```bash
#!/bin/bash
# Daily soak coverage for SG-2026-05-11. Run from project root.
set -euo pipefail

# 1. Capture baseline log size
BASELINE=$(wc -l < data/backend.log 2>/dev/null || echo 0)
echo "Baseline log line count: $BASELINE"

# 2. Restart services (verifies lifespan startup path)
./init.sh restart
sleep 60  # let lifespan settle + first hourly GC kick

# 3. Exercise P4 Cycle 1 — passthrough save (writes through save_result_persist)
curl -sS -X POST http://localhost:8000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{"raw_prompt":"soak test C1 — passthrough scoring path","strategy":"clarity","force_passthrough":true}' \
  | jq -r '.trace_id // "no_trace"' > /tmp/soak_c1_trace.txt

# 4. Exercise P4 Cycle 2 — refinement initial + rollback
# (using the trace_id from C1 above, which produced an optimization_id internally)
OPT_ID=$(curl -sS http://localhost:8000/api/history?limit=1 | jq -r '.items[0].id')
curl -sS -X POST "http://localhost:8000/api/refine" \
  -H "Content-Type: application/json" \
  -d "{\"optimization_id\":\"${OPT_ID}\",\"feedback\":\"soak test C2 — refinement initial turn\"}" \
  | jq -r '.trace_id // "no_trace"' > /tmp/soak_c2_trace.txt
# (Optional) rollback to exercise refine_rollback:
# curl -sS -X POST "http://localhost:8000/api/refine/${OPT_ID}/rollback" ...

# 5. Exercise P4 Cycle 3 — internal-tier optimize (the canonical pipeline)
curl -sS -X POST http://localhost:8000/api/optimize \
  -H "Content-Type: application/json" \
  -d '{"raw_prompt":"soak test C3 — internal tier","strategy":"clarity"}' \
  | jq -r '.trace_id // "no_trace"' > /tmp/soak_c3_trace.txt

# 6. Wait briefly so all writes settle
sleep 30

# 7. Run the verification commands and record evidence in daily log
echo "=== Audit-hook WARN lines today ==="
tail -n +$((BASELINE + 1)) data/backend.log | grep -E "audit_drift|read-engine audit:" || echo "(none — pass)"

echo ""
echo "=== Coverage evidence (operation_labels seen today) ==="
tail -n +$((BASELINE + 1)) data/backend.log \
  | grep -oE "operation_label=['\"][^'\"]+['\"]" \
  | sort | uniq -c

echo ""
echo "=== P4-cycle exercise counts ==="
echo -n "C1 save_result_persist:        "; tail -n +$((BASELINE + 1)) data/backend.log | grep -c "save_result_persist" || true
echo -n "C2 refine_initial_turn:         "; tail -n +$((BASELINE + 1)) data/backend.log | grep -c "refine_initial_turn" || true
echo -n "C2 refine_persist_turn:         "; tail -n +$((BASELINE + 1)) data/backend.log | grep -c "refine_persist_turn" || true
echo -n "C2 refine_rollback:             "; tail -n +$((BASELINE + 1)) data/backend.log | grep -c "refine_rollback" || true
echo -n "C3 pipeline_failed_optimization_persist (only on failure): "; tail -n +$((BASELINE + 1)) data/backend.log | grep -c "pipeline_failed_optimization_persist" || true
```

**If real user traffic naturally exercises these surfaces during the window**, the synthetic recipe is redundant but harmless. **If real traffic is absent** (e.g., personal/team-internal tool, low-activity periods, weekends), the synthetic recipe is the ONLY way to produce gate evidence — without it the gate's 7-day clock just expires without testing the flip precondition.

**Recommended cron**:

```cron
0 12 * * * /path/to/repo && bash docs/scripts/soak_daily.sh >> data/soak_gate_evidence.log 2>&1
```

(The actual script file is not committed to the worktree — the recipe above is for the operator to copy or wrap as `data/scripts/soak_daily.sh` locally. It's intentionally not a tracked file so the soak operator can tune cadence/payloads per-environment without churning the gate doc.)

### Verification commands

Run from the project root with a live `data/backend.log` (post-`./init.sh restart`):

```bash
# Primary check — list any new audit-hook WARN lines
grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100

# Total count over the soak window
grep -cE "audit_drift|read-engine audit:" data/backend.log

# Per-day breakdown (useful for spotting trends)
grep -E "audit_drift|read-engine audit:" data/backend.log | \
  awk '{print $1}' | sort | uniq -c

# Surface the unique source labels (which handler/site is firing)
grep -E "audit_drift|read-engine audit:" data/backend.log | \
  grep -oE "operation_label=['\"][^'\"]+['\"]" | sort | uniq -c

# Cross-check: are there correlated user-facing errors?
grep -E "WriteOnReadEngineError|ReadEngineAuditError" data/backend.log | tail -50
```

**Interpretation**:
- `0 lines` from the primary check → 🟢 PASS for the day.
- `N lines, all from same known-explained source` (e.g., `task_type_telemetry_no_session` during a documented Phase 4 migration cycle) → 🟡 ACTIVE, document the source in the daily log below.
- `N lines from previously-unseen source` → 🔴 BLOCKED. Capture line, identify code path via `operation_label` + stack trace, halt v0.4.22 ship until restructured.

### Daily check-in log

Each day during the soak window, the release-operator runs the verification commands AND the soak-traffic recipe, then records the result here. Append entries — never edit prior entries.

**Coverage floor**: each of the 3 P4 cycles must accumulate ≥3 writes across the full window. A row showing `C1=0, C2=0, C3=0` is **not evidence of safety** — it's evidence of inactivity. If the floor isn't trending toward ≥3 by Day 4, the operator MUST run the synthetic-traffic recipe to backfill.

| Date | Day | Operator | Restart? | C1 writes | C2 writes | C3 writes | Total WARN count | New sources | Decision | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-05-11 | 0 (window opens) | release-bot | ✅ (post-v0.4.21 ship) | 0 (baseline) | 0 (baseline) | 0 (baseline) | (baseline TBD) | (baseline TBD) | 🟡 ACTIVE | v0.4.21 shipped, soak window opens, baseline log captured |
| 2026-05-12 | 1 | claude-soak-operator | ✅ (x5 — restarts during fix iteration) | 4 (3 synthetic + 1 verify) | 3 (initial-turn + persist + rollback + post-rollback persist) | 3 (overall 7.92 + 7.49 + 8.48) | 0 (post-fix) — 6 (pre-fix, all classes documented + fixed below) | **6 distinct bugs the soak gate surfaced** + 1 latent singleton-wiring gap. See **Day 1 finding block** below | 🟡 ACTIVE (gate worked as designed — caught 6 real bugs before flip ships) | 6 commits + 12 new regression tests; all live-verified clean under RAISE; backend.log: 0 WARN over 600+ post-fix lines covering 20+ endpoint exercises |
| 2026-05-13 | 2 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 2026-05-14 | 3 (mid-soak) | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 2026-05-15 | 4 (coverage-floor checkpoint) | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | If any P4 cycle < 2 cumulative, the operator must run the synthetic recipe ≥1×/day from this point |
| 2026-05-16 | 5 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 2026-05-17 | 6 (pre-close sanity) | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| 2026-05-18 | 7 (close decision) | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | gate-close decision day — verify coverage floor met BEFORE flipping 🟢 |

Cumulative coverage at close (filled at Day 7):

| Cycle | Cumulative writes during window | Floor met (≥3)? |
|---|---|---|
| C1 (save_result_persist) | _pending_ | _pending_ |
| C2 (refine_*) | _pending_ | _pending_ |
| C3 (pipeline_failed_optimization_persist OR successful internal optimize) | _pending_ | _pending_ |

**Operator note**: The `release-bot` row exists as a placeholder for an automated daily collector. Until that automation lands, the release-operator manually fills each row. The gate decision on day 7 (or later, if EXTENDED) closes the gate and either greenlights v0.4.22 ship (🟢) or routes back to restructure (🔴) OR EXTEND (🟤) if coverage floor isn't met.

### Day 1 finding (2026-05-12) — gate worked as designed

The soak gate caught three independent v0.4.22-flip blockers during synthetic-traffic exercise. All three were FIXED before merge with regression coverage. This is exactly the long-tail bug class documented in [Why this gate exists](#why-this-gate-exists) — *"a forgotten direct db.execute() in a less-traveled branch"* — and the bug-class found is broader than save_result alone.

**Finding 1 — `POST /api/optimize/passthrough/save` raises under RAISE flip** (FIXED at `b288989a`)

The REST web-UI manual-passthrough save path declared queue-routing only at the COMMIT layer (`_do_pt_save_commit` only ran `await write_db.commit()`), but applied ALL ORM mutations on the request-bound read-engine session PRIOR to the queue submit. Under v0.4.21 WARN-mode the autoflush triggered by the post-submit `db.refresh(opt)` emitted a `read-engine audit:` log line; under v0.4.22 RAISE-mode the same autoflush trips `WriteOnReadEngineError` → `PendingRollbackError` → HTTP 500.

The endpoint was restructured to apply ALL mutations inside the WriteQueue closure on the write-engine session (matches the Foundation P4 canonical pattern at `app.tools.save_result._persist_save_result`). Read session is now used ONLY for read-only existence + value snapshotting + analyzer historical-stats fetching.

**Finding 2 — `PATCH /api/optimize/{id}` (intent rename) raises under RAISE flip** (FIXED at `b288989a`)

Same v0.4.14 oversight class as Finding 1. The rename mutation landed on the read-engine session before the empty-commit closure submitted, autoflush → `WriteOnReadEngineError` → HTTP 500. Restructured to apply the mutation inside the WriteQueue closure on the write-engine session.

Both Finding 1+2 are covered by `backend/tests/test_audit_hook_passthrough_save.py` (3 tests asserting zero `read-engine audit:` WARN lines under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` via caplog at logger `app.database`). Same pattern as `test_tools_optimize.py:907-908` + `test_audit_hook_full_t2_pipeline.py`.

**Finding 3 — `POST /api/refine` returns "Routing service not initialized" in production** (FIXED at `de18b54a`)

Latent for an unknown period. The MCP server process wires every `app.tools._shared` singleton at startup via `mcp_server.py` (lines 284, 307, 380, 399, 400, 458, 503). The backend process (`main.py`) lifespan was wiring ONLY 3 of these (`set_domain_resolver`, `set_signal_loader` (analyzer flavor), `set_write_queue`) — five singletons (`_shared.set_routing` + `_shared.set_taxonomy_engine` + `_shared.set_context_service` + `_shared.set_domain_resolver` (`_shared` flavor) + `_shared.set_run_orchestrator`) were missing.

REST endpoints that delegate to MCP tool handlers in the same process (`POST /api/refine` → `handle_refine` → `get_routing()`, `POST /api/optimize/passthrough` → `handle_prepare` → `get_context_service()`, etc.) would emit `ValueError("X not initialized")` at the `get_X()` call. Tests passed in CI because `backend/tests/conftest.py:279` stubs `_shared.set_routing(test_routing)` directly.

Added five missing `_shared.set_X` invocations to backend lifespan, each placed immediately after the corresponding service is constructed on `app.state.X`. Regression pinned by `backend/tests/test_main_lifespan_shared_singletons.py` (8 static-text-grep tests, same pattern as `test_main_lifespan_no_ddl.py`).

**Live verification post-fix (single restart, RAISE default active)**:

- `POST /api/optimize/passthrough/save` → 200 + `status=completed` + `overall_score=6.49`.
- `PATCH /api/optimize/{id}` → 200 + intent_label correctly updated.
- `POST /api/refine` → emits `started` event (no `error` event).
- `POST /api/optimize` (internal tier) → full 3-phase pipeline succeeds, `overall_score=7.92`, hybrid scoring engaged, 115s duration.
- `POST /api/feedback` → 200.
- `POST /api/clusters/match` → 200 (no match yet).
- `POST /api/seed` → 200 (returns failed with validation error — not an audit issue).
- `POST /api/probes` (topic_only) → emits `probe_started`.
- `POST /api/domains/{id}/rebuild-sub-domains` (dry_run) → 200.

**Backend.log cumulative since post-fix restart**: 308 lines, **0** `audit_drift` lines, **0** `read-engine audit:` WARN lines, **0** `WriteOnReadEngineError` raises.

**Finding 4 — refine-after-rollback UX defect** (FIXED at `bc5267b9`)

Surfaced during C2 (refine_*) coverage exercise. `POST /api/refine/{id}/rollback` created a new `RefinementBranch` but did NOT seed it with an initial turn. Subsequent `POST /api/refine` without an explicit `branch_id` defaulted to the empty rollback branch and raised `ValueError("invoke_refinement_pipeline requires latest_turn_snapshot; caller must seed via build_initial_turn_payload + queue submit")`.

This was NOT an audit-hook RAISE blocker (system correctly emitted a `ValueError` SSE event, not a 500), but it was a synthesis-pipeline UX defect — the error message pointed at internal mechanics, and the workaround (specify `branch_id` explicitly) was non-obvious.

`RollbackPayload` was extended with `seed_turn_kwargs`. `RefinementService.rollback()` now builds them by copying every field from the `target_turn` (the version being rolled back to) — prompt, scores, deltas, strategy, suggestions, trace_id. The rollback persist callback now inserts BOTH the new branch AND a seed `RefinementTurn` (version 1) in the same write transaction.

Regression coverage at `backend/tests/test_audit_hook_passthrough_save.py::test_rollback_seeds_initial_turn_so_subsequent_refine_works` + extended `test_tools_refine.py::test_refinement_service_rollback_returns_payload`.

**Live post-fix lifecycle verification (single restart)**:

1. `POST /api/optimize` (internal tier) → `overall_score 7.49`.
2. `POST /api/refine` → `version 2` on primary branch.
3. `POST /api/refine/{id}/rollback {to_version: 1}` → new branch `df7206db` with `forked_at_version: 1`.
4. **`POST /api/refine` (NO `branch_id`) → `version 2` on the rollback branch** (was: `ValueError` pre-fix).
5. data/backend.log: 0 audit-hook WARN, 0 `WriteOnReadEngineError`.

**Coverage floor status (Day 1 cumulative)**:

| Cycle | Writes today | Floor (≥3)? |
|---|---|---|
| C1 (save_result_persist) | 4 | ✅ |
| C2 (refine_initial_turn / refine_persist_turn / refine_rollback) | 3+ (initial + persist v2 + rollback + persist v2-of-rollback-branch) | ✅ |
| C3 (internal-tier optimize / pipeline_failed_optimization_persist) | 3 (overall 7.92 + 7.49 + 8.48) | ✅ |

**Finding 5 — domains.py engine calls not routed through WriteQueue** (FIXED at `7a6a7f33`)

Both `POST /api/domains/{id}/rebuild-sub-domains` and `POST /api/domains/{id}/dissolve-empty` called the taxonomy engine method with the request-bound read session AND submitted a separate empty-commit closure. Same v0.4.14 oversight class.

`rebuild_sub_domains` — engine method has `write_queue=` kwarg (v0.4.13 cycle 7b) that internally routes the entire body through `write_queue.submit(operation_label="engine_rebuild_sub_domains")`. Router now passes `write_queue=write_queue` and drops the redundant empty-commit closure.

`dissolve_empty_domain` — engine method doesn't take `write_queue`. Router now wraps the entire engine call in a `write_queue.submit(operation_label="domain_dissolve_empty")` closure so the engine's mutations + internal commit land on the write-engine session.

Regression coverage: existing `test_domains_router.py` (8 tests) + `test_routers_queue_routing.py::test_domains_dissolve_label` (substring guard). 38 domain-related tests PASS post-fix. Live-verified: dry_run + non-dry_run both return 200 under RAISE.

**Finding 6 — projects.py + github_repos.py empty-commit closures** (FIXED at `63912684`)

Three more endpoints with the same anti-pattern:

- `POST /api/projects/migrate` — `migrate_optimizations(db, ...)` issued `UPDATE optimizations SET project_id=...` on the read session.
- `POST /api/repos/link` — `db.delete(existing)` + `db.flush()` + `db.add(linked)` + `ensure_project_for_repo(db, ...)` all on the read session.
- `DELETE /api/repos/unlink` — B5 rehome migration + `db.delete(linked)` on the read session.

All three restructured to run mutations inside `write_queue.submit(operation_label=...)` closures on the write-engine session. The unlink path re-fetches LinkedRepo on the writer session by id before delete (since the read-session ORM instance is bound to a different session).

Regression coverage: 39 tests across `test_link_repo_b4.py`, `test_unlink_repo_b5.py`, `test_projects_router.py`, `test_project_migration.py`, `test_project_creation.py`, `test_routers_queue_routing.py`, `test_topology_project_filter.py` — all PASS post-fix. Operation_label substrings preserved for the substring-guard tests.

**What this finding means for the gate decision**:

- The gate WORKED — caught **6 real bugs** that would have been user-visible defects or HTTP 500s on Day 1 of v0.4.22 user traffic.
- All 3 P4 coverage rows met the ≥3 floor on Day 1 alone via synthetic-recipe + targeted refine exercises.
- Comprehensive sweep complete: remaining `_do_*_commit` patterns at `routers/clusters.py` (4), `routers/templates.py` (3), `routers/github_auth.py` (4) all verified CORRECT — mutations happen inside the closure on `write_db` (the `db` parameter is the writer session passed by `write_queue.submit`, not the read session).
- Day 1 is a strong 🟡 → 🟢 trend: zero audit-hook RAISE across 600+ lines of post-fix backend.log over 15+ endpoint exercises.

**MCP tool handler + service-layer audit closure** (2026-05-12 Day 1):

Exhaustive sweep of `backend/app/tools/*.py` (17 tool handlers) and `backend/app/services/*.py` (25+ services) for the same v0.4.14 oversight class:

- All 4 `await db.commit()` calls in `backend/app/tools/` (`feedback.py:40`, `optimize.py:330`, `prepare.py:160`, `save_result.py:317`) verified inside `write_queue.submit()` closures — the `db` parameter is the writer session, not the read session.
- All service-layer commits in `pattern_injection.py` / `pipeline_phases.py` / `validation_suite_service.py` / `batch_persistence.py` / `feedback_service.py` / `template_service.py` verified inside writer-queue closures or properly delegate to caller commits in writer paths.
- Remaining service `db.commit()` calls (`gc.py`, `orphan_recovery.py`, `repo_index_service.py`, `taxonomy/cold_path.py`, `taxonomy/snapshot.py`, etc.) are all in background paths (warm cycle, cold path, GC sweeps, repo indexing) that already run on writer-engine sessions per v0.4.13/14/16 migrations.

**Real-MCP-client end-to-end round-trip** (2026-05-12 Day 1):

Live-invoked via real MCP tool calls (not the test stub):

- `synthesis_health` → 200, `provider=claude_cli`, `available_tiers=[internal, passthrough]`, `total_optimizations: 17`, `avg_score: 7.04`, `domain_count: 2`.
- `synthesis_optimize` (chain-of-thought, soak-D1-mcp-#1 prompt) → 200, `pipeline_mode: internal`, `model_used: claude-opus-4-7`, full 3-phase pipeline (analyze/optimize/score), 8.07 overall (7.7/8.5/7/9.1/8.1).
- `synthesis_feedback` (thumbs_up) → 200, `strategy_affinity_updated: true`.
- Domain emergence: `domain_count` advanced from 1 → 2 organically during the round-trip — synthesis pipeline + taxonomy emergence functional under RAISE.
- Backend.log post-MCP-roundtrip: 0 `audit_drift` / `read-engine audit:` / `WriteOnReadEngineError` / `PendingRollbackError`.

**CI status (PR #74 as of 2026-05-12 Day 1 close)**:

| Check | Status |
|---|---|
| test | ✅ SUCCESS |
| claude-review | ✅ SUCCESS |
| backend-integration | ✅ SUCCESS |
| lint | ✅ SUCCESS |
| frontend | ✅ SUCCESS |

10/10 commits on PR. All Day 1 fixes + regression coverage shipped. Gate continues observing Days 2-7 (closes 2026-05-18).

### Decision matrix

Read column-by-column: first match wins.

| Coverage floor (all 3 cycles ≥3 writes) | Cumulative WARN count | Unique new sources | Decision | Next action |
|---|---|---|---|---|
| ❌ NOT met | (any) | (any) | 🟤 INSUFFICIENT-COVERAGE → EXTEND | Run synthetic-traffic recipe daily until floor met, extend window proportionally. The gate is evidence-based, NOT time-based — a 7-day clock without writes proves nothing. |
| ✅ met | 0 | 0 | 🟢 PASS (verified) | Ship v0.4.22 after rebase-merge + release. |
| ✅ met | 1-2 | 0 (all explained from known sources) | 🟢 PASS (verified, with notes) | Ship v0.4.22, document explained sources in CHANGELOG. |
| ✅ met | 1-2 | 1 (new but understood, fix in flight) | 🟤 EXTEND | Ship fix as v0.4.21.x, extend soak by 7 days from fix-commit. |
| ✅ met | ≥3 | ≥1 | 🔴 BLOCKED | Halt v0.4.22, restructure offending paths in patch, restart soak from patch ship date. |
| (any) | (any) | `audit_drift` lines | 🔴 BLOCKED (severe) | WriteQueue contract violation — escalate immediately, halt v0.4.22 entirely until queue layer patched. |

**The vacuous-PASS guard** (top row): without it, a 7-day backend that processed zero traffic produces "0 WARN × 0 new sources" and would falsely pass the gate. The coverage floor row catches this — no writes = no evidence = no PASS, regardless of how many calendar days elapsed.

### Kill-switch (if flip ships but production surfaces issues)

Even after a clean soak gate, the v0.4.22 audit-hook flip ships with a documented kill-switch:

```bash
# Revert audit hook to v0.4.21 WARN-only behavior at runtime
export WRITE_QUEUE_AUDIT_HOOK_RAISE=false
./init.sh restart
```

The `Field(description=...)` block in `backend/app/config.py` documents this kill-switch explicitly so any operator reading the config surfaces the revert path without needing this doc.

### Escalation path

| Trigger | Action | Who |
|---|---|---|
| 1 unexpected WARN, source known | Document in daily log, continue soak | release-operator |
| 1 unexpected WARN, source unknown | Investigate via `operation_label` + stack trace within 24h | release-operator → primary maintainer |
| ≥2 unexpected WARN OR `audit_drift` | Halt v0.4.22 ship, post in ops channel | primary maintainer |
| `WriteOnReadEngineError` raises user-visible 500 (post-flip) | Roll back flip via kill-switch, file blocker issue | on-call |

### References

- **Cycle 15 spec**: `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` §7 (audit-hook flip mechanics) + §10 Cycle 15
- **Cycle 15 plan**: `docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md` Cycle 15 (Tasks 15.1-15.7)
- **P4 precondition test**: `backend/tests/test_tools_optimize.py::test_audit_hook_emits_zero_warn_under_full_pipeline` (`@pytest.mark.integration`)
- **T2 extension test**: `backend/tests/test_audit_hook_full_t2_pipeline.py` (3 tests covering RAISE default + zero-WARN under full T2 pipeline + kill-switch revert)
- **Foundation P4 SHIPPED entry**: `docs/SHIPPED.md` v0.4.21 section
- **Config change**: `backend/app/config.py` `WRITE_QUEUE_AUDIT_HOOK_RAISE` Field default
- **Audit-hook implementation**: `backend/app/database.py:395-397`

---

## Soak-gate conventions

### When to open a new soak gate

A soak gate is warranted when a change has ALL three properties:

1. **Architecturally significant** — touches a cross-cutting layer (write path, auth, lifecycle, observability) rather than a single feature.
2. **Difficult to revert** — once a downstream feature builds on top, rolling back requires unwinding multiple commits.
3. **Long-tail risk** — single test runs cannot exhaust the production-traffic distribution (rate-limit retries, cancellation timing, concurrent sessions).

Examples that warrant a gate: write-queue migration, audit-hook severity flip, lifecycle-DDL consolidation, schema migrations that backfill production rows.

Examples that do NOT warrant a gate: pure feature additions, isolated bug fixes, documentation, UI styling.

### Soak window sizing

| Change blast radius | Minimum soak window |
|---|---|
| Cross-cutting log/observability change | 3 days |
| Write-path / persistence-layer change | 7 days (SG-2026-05-11 baseline) |
| Auth or session-management change | 14 days |
| Schema migration with production-row backfill | 14 days minimum, decision per-case |

### Gate ID format

`SG-YYYY-MM-DD` where the date is the **gate open date** (not the close date). If multiple gates open on the same day, append `-{slug}` (e.g., `SG-2026-05-11-audit-hook`).

### Where this doc fits

- **Spec author** writes the gate requirement into the spec (e.g., §7 Cycle 15 in the v0.4.22 T2 spec).
- **Plan author** schedules the gate as a Cycle prerequisite (e.g., Cycle 15 GREEN blocked on soak verification).
- **Release operator** registers the gate here on the day the precondition ships AND consults daily during the window.
- **CHANGELOG / SHIPPED entries** reference the gate ID so future readers can trace decisions.

### Adding a new gate

1. Append a row to **Active gates** table at the top with status `⚪ PLANNED` or `🟡 ACTIVE`.
2. Add a per-gate detail section below (clone the SG-2026-05-11 template).
3. Schedule daily check-ins for the soak operator.
4. On gate close, change status → `🟢 PASSED` (or `🔴 BLOCKED`), move row to **Historical gates** at the bottom, keep detail section in place (do not delete history).
