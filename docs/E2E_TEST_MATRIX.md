# E2E Test Matrix — v0.4.22 Topic Probe Tier 2

Actionable end-to-end verification checklist for the Topic Probe Tier 2 feature set. Maps every user/system **action** to every plausible **error condition** + the expected response + the verification command + the responsible test (or operator check if not auto-covered).

This document is the **operator's checklist** for pre-ship verification. Run alongside the [SG-2026-05-11 soak gate tracker](SOAK_GATES.md#sg-2026-05-11--audit-hook-warnrise-flip).

## How to use this document

1. **During development** — author confirms each row's verification PASSES before marking the related Cycle complete.
2. **Pre-PR** — Cycle 14/16 validators audit unfilled rows.
3. **Pre-merge** — release operator walks the **Full-stack flow checklist** at the bottom + checks the soak gate.
4. **Post-merge / pre-tag** — release operator re-runs the resilience block + Stage 7 soak grep.
5. **Post-release** — operator re-runs the kill-switch test + monitors `data/backend.log` for 7 days.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Auto-covered by an existing test that PASSES |
| 🧪 | Manual operator step (cannot be fully automated; instructions inline) |
| 🟡 | Conditional / depends on environment (e.g., live LLM provider) |
| 🚫 | Deferred to T3/T4 (out of v0.4.22 scope) |
| 🔄 | Re-run required at every release |

---

## Action × Error matrix

Compact view: rows are user/system actions, columns are failure modes. Cell value = test/scenario ID covering that intersection (or `—` if not applicable).

### Backend write surface

| Action ↓ \ Error → | run_not_found | run_not_completed | not_a_probe_run | run_missing_aggregate | invalid_label | invalid_tolerance | suite_not_found | suite_retired | invalid_reason | rate_limit | network/LLM fault | cancellation mid-flight | concurrent caller | audit-hook RAISE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `POST /api/probes/{run_id}/save-as-suite` | B1.1 ✅ | B1.2 ✅ | B1.3 ✅ | B1.4 ✅ | B1.5 ✅ | B1.6 ✅ | — | — | — | B1.7 ✅ | B1.8 🧪 | B1.9 🧪 | B1.10 ✅ | B1.11 ✅ |
| `GET /api/suites` | — | — | — | — | — | — | — | — | — | — | B2.1 🟡 | — | B2.2 ✅ | — |
| `GET /api/suites/{suite_id}` | — | — | — | — | — | — | B3.1 ✅ | — | — | — | — | — | — | — |
| `GET /api/suites/{suite_id}/replays` | — | — | — | — | — | — | B4.1 ✅ (empty envelope, NOT 404) | — | — | — | — | — | — | — |
| `POST /api/suites/{suite_id}/replay` | — | — | — | — | — | — | B5.1 ✅ | B5.2 ✅ | — | B5.3 ✅ | B5.4 🧪 | B5.5 🧪 | B5.6 ✅ | B5.7 ✅ |
| `POST /api/suites/{suite_id}/retire` | — | — | — | — | — | — | B6.1 ✅ | — | B6.2 ✅ | B6.3 ✅ | — | B6.4 🧪 | B6.5 🧪 documented divergence (Cycle 3 OPERATE Scenario F) | B6.6 ✅ |
| `GET /api/health` | — | — | — | — | — | — | — | — | — | — | B7.1 ✅ (3-path safe-default) | — | B7.2 ✅ (30s TTL) | B7.3 ✅ |
| `POST /api/probes` (SSE) | — | — | — | — | — | — | — | — | — | B8.1 ✅ | B8.2 🧪 | B8.3 ✅ | B8.4 🧪 | — |
| `POST /api/probes` (`Prefer: respond-async`) | — | — | — | — | — | — | — | — | — | B9.1 ✅ | B9.2 🧪 | B9.3 ✅ (asyncio.shield) | B9.4 ✅ | — |

### MCP tool surface

| Action ↓ \ Error → | run_not_found | run_not_completed | not_a_probe_run | run_missing_aggregate | suite_not_found | suite_retired | dispatch unknown_mode | concurrent client | cross-process state |
|---|---|---|---|---|---|---|---|---|---|
| `synthesis_save_suite` | M1.1 ✅ | M1.2 ✅ | M1.3 ✅ | M1.4 ✅ | — | — | — | M1.5 🧪 | M1.6 ✅ |
| `synthesis_replay_suite` | — | — | — | — | M2.1 ✅ | M2.2 ✅ | M2.3 ✅ (Cycle 10 critical fix) | M2.4 🧪 | M2.5 ✅ |
| `synthesis_probe` (existing T1) | — | — | — | — | — | — | — | M3.1 ✅ | M3.2 ✅ |

### Frontend surface

| Action ↓ \ Error → | network error | server 5xx | server 4xx error envelope | empty result | retired-suite state | keyboard/a11y | reduced-motion |
|---|---|---|---|---|---|---|---|
| SuitesPanel render | F1.1 ✅ | F1.2 🧪 | F1.3 ✅ (panel-error) | F1.4 ✅ (empty-state copy) | F1.5 ✅ (data-retired dimming) | F1.6 ✅ (focus-visible + role) | F1.7 ✅ |
| SuiteRow click → SuiteDetailView | — | — | — | — | F2.1 ✅ | F2.2 ✅ (real `<button>`) | — |
| SuiteDetailView Replay button | F3.1 ✅ | F3.2 🧪 | F3.3 ✅ (toast.error) | — | F3.4 ✅ (button hidden on retired) | F3.5 🧪 | — |
| SuiteDetailView Retire button | F4.1 ✅ | F4.2 🧪 | F4.3 ✅ (toast.error) | — | F4.4 ✅ (button hidden when `retired_at != null`) | F4.5 ✅ (DestructiveConfirmModal `RETIRE` literal gate) | — |
| RegressionBadge state-change | — | — | — | F5.1 ✅ (suites_total=0 → no-badge) | — | F5.2 ✅ (`role="status"` + `aria-live="polite"`) | F5.3 ✅ (universal `prefers-reduced-motion` override) |
| StatusBar mount | — | — | — | — | — | — | — |
| SeedModal 3rd tab → TopicProbeForm → submit | F6.1 ✅ | F6.2 🧪 | F6.3 ✅ (state → 'failed') | — | — | F6.4 ✅ | F6.5 ✅ |
| TopicProbeProgressView events render | F7.1 ✅ (source-agnostic SSE/poll) | — | F7.2 🧪 | F7.3 ✅ | — | — | F7.4 ✅ (forge-spark 0.01ms under reduced-motion) |
| TopicProbeReportCard Save-as-Suite | F8.1 ✅ | F8.2 🧪 | F8.3 ✅ (toast.error) | — | — | F8.4 ✅ | F8.5 ✅ (forge-spark animation reduced-motion-safe) |
| TopicProbeReportCard Copy-md | — | — | — | — | — | F9.1 ✅ (aria-label on icon-only button) | F9.2 ✅ (copy-flash) |
| TopicProbeReportCard Replay | F10.1 ✅ | F10.2 🧪 | F10.3 ✅ (toast.error) | — | — | F10.4 ✅ | — |
| probesStore.runProbe abort | — | — | — | — | — | — | — |
| Browser tab close mid-probe | F11.1 ✅ (AbortSignal threading) | — | — | — | — | — | — |

### Resilience surface

| Action ↓ \ Error → | caller cancellation | per-task timeout | concurrent writer | WAL contention | orphan reconciliation | restart mid-flight | rate-limit | audit-hook RAISE |
|---|---|---|---|---|---|---|---|---|
| Topic probe in-flight | R1.1 ✅ (asyncio.shield) | R1.2 ✅ | R1.3 ✅ (WriteQueue serialization) | R1.4 ✅ (WAL isolation) | R1.5 ✅ (`_gc_orphan_runs` 1h TTL) | R1.6 🧪 | R1.7 ✅ | R1.8 ✅ |
| Replay run in-flight | R2.1 ✅ | R2.2 ✅ | R2.3 ✅ | R2.4 ✅ | R2.5 ✅ (covers `mode='replay_run'` rows) | R2.6 🧪 | R2.7 ✅ | R2.8 ✅ |
| Save-as-suite write | R3.1 ✅ (state consistent: row absent OR row complete, never half-baked) | R3.2 ✅ | R3.3 ✅ | R3.4 ✅ | — | — | R3.5 ✅ | R3.6 ✅ |
| Concurrent retire on same suite | R4.1 ✅ | — | R4.2 🟡 (documented divergence: 5 callers emit 5 events; spec §9 silent on concurrent semantics — see Cycle 3 OPERATE) | — | — | — | R4.3 ✅ | R4.4 ✅ |
| Regression alarm compute under concurrent replay writes | R5.1 ✅ (Cycle 4 OPERATE Scenario F) | — | R5.2 ✅ | R5.3 ✅ | — | — | — | — |
| Health endpoint under degraded service | R6.1 ✅ (3-path safe-default → never 5xx) | — | — | — | — | — | — | — |

---

## Test ID detail blocks

### B1 — `POST /api/probes/{run_id}/save-as-suite`

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| B1.1 | POST with nonexistent run_id | run_id=`nonexistent-uuid` | 404 + `{detail: "run_not_found"}` | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_run_not_found_returns_404` | ✅ |
| B1.2 | POST while run is still running | seed `RunRow(status='running')`, POST | 409 + `{detail: "run_not_completed"}` | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_combined_409s[running_run]` | ✅ |
| B1.3 | POST against seed_agent run | seed `RunRow(mode='seed_agent', status='completed')`, POST | 400 + `{detail: "not_a_probe_run"}` (NOT 409 per spec) | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_combined_409s[seed_agent_run]` | ✅ |
| B1.4 | POST when aggregate is None | seed `RunRow(mode='topic_probe', status='completed', aggregate=None)`, POST | 409 + `{detail: "run_missing_aggregate"}` | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_combined_409s[missing_aggregate]` | ✅ |
| B1.5 | POST with invalid label (empty or >120 chars) | body `{label: "", tolerance_abs: 0.5}` | 400 + `{detail: "invalid_label"}` | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_invalid_label_returns_400` | ✅ |
| B1.6 | POST with tolerance outside [0.1, 5.0] | body `{label: "x", tolerance_abs: 0.05}` | 400 + `{detail: "invalid_tolerance"}` | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_invalid_tolerance_returns_400` | ✅ |
| B1.7 | Rate limit 20/min | issue 25 rapid POSTs | first 20 succeed, remainder return 429 | `pytest backend/tests/test_routers_suites.py::test_save_as_suite_rate_limit_20_per_minute_returns_429_after_threshold` | ✅ |
| B1.8 | Network fault → upstream LLM returns 502 | live test: kill upstream provider mid-pipeline | service `ValueError` propagates, 500 with logged trace | manual: stop provider during in-flight pipeline | 🧪 |
| B1.9 | Caller cancellation during write | `httpx.AsyncClient.send(stream=False)` + `task.cancel()` 1ms after send | row consistent — either fully created OR not created, never partial | manual via curl `--max-time 0.5`; verified by Cycle 5 OPERATE Scenario B + Cycle 2 OPERATE Scenario F | 🧪 |
| B1.10 | Concurrent same-run callers | 5 concurrent POSTs on same run_id (different labels) | All 5 succeed → 5 distinct suites (no uniqueness on source_run_id per spec) | `pytest backend/tests/test_validation_suite_service.py::test_create_from_run_*` covers via service-layer race | ✅ |
| B1.11 | Audit-hook RAISE post-flip | flip `WRITE_QUEUE_AUDIT_HOOK_RAISE=True`, save-as-suite | Zero `read-engine audit:` WARN lines in caplog | `pytest backend/tests/test_audit_hook_full_t2_pipeline.py::test_audit_hook_emits_zero_warn_under_full_t2_pipeline` | ✅ |

### B2 — `GET /api/suites`

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| B2.1 | DB transient error mid-query | manual: drop network to SQLite WAL | 5xx + structured error, NOT silent empty list | manual SRE drill | 🟡 |
| B2.2 | Concurrent listings during active write | 5 reader coroutines + 1 writer coroutine for 3s | Zero reader exceptions, total monotonic | Cycle 5 OPERATE Scenario D + `pytest backend/tests/test_routers_suites.py::test_get_suites_paginated_envelope_shape` | ✅ |

### B5 — `POST /api/suites/{suite_id}/replay`

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| B5.1 | POST against nonexistent suite | suite_id=`nonexistent` | 404 + `{detail: "suite_not_found"}` | `pytest backend/tests/test_routers_suites.py::test_replay_suite_not_found_or_retired_combined[suite_not_found]` | ✅ |
| B5.2 | POST against retired suite | seed retired suite, POST | 409 + `{detail: "suite_retired"}` | `pytest backend/tests/test_routers_suites.py::test_replay_suite_not_found_or_retired_combined[suite_retired]` | ✅ |
| B5.3 | Rate limit 5/min | 10 rapid POSTs | first 5 succeed, remainder 429 | `pytest backend/tests/test_routers_suites.py::test_replay_rate_limit_5_per_minute_returns_429` | ✅ |
| B5.4 | curl `--max-time 0.1` mid-INSERT | live curl with short timeout | Either row exists in `status='running'` (then GC'd by `_gc_orphan_runs` within 1h) OR no row | manual SRE drill | 🧪 |
| B5.5 | Mid-replay cancellation | spawn replay, send SIGTERM to backend after prompt 2 of 5 | `RunRow.status` reaches `'failed'` (asyncio.shield write) | manual SRE drill | 🧪 |
| B5.6 | Concurrent replays on different suites | 3 concurrent replays on 3 distinct suites | All 3 complete, 3 distinct run_ids, 3 `replay_run_persist` op-labels | Cycle 8 OPERATE Scenario F + WriteQueue serialization | ✅ |
| B5.7 | Audit-hook RAISE post-flip | flip on, replay | Zero WARN | `pytest backend/tests/test_audit_hook_full_t2_pipeline.py::test_audit_hook_emits_zero_warn_under_full_t2_pipeline` (stage 6) | ✅ |

### B7 — `GET /api/health`

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| B7.1 | Service unavailable / exception | manual: `app.state.validation_suite_service = None` OR raise inside `compute_regression_alarm` | 200 + canonical empty `regression_alarm` block (3-path safe-default), NEVER 5xx | `pytest backend/tests/test_health_regression_alarm.py` + Cycle 9 OPERATE Scenario F | ✅ |
| B7.2 | 30s TTL cache | call `/api/health` twice within 30s | Alarm SQL fires once | `pytest backend/tests/test_health_regression_alarm.py::test_health_regression_alarm_30s_ttl_cache_respected` | ✅ |
| B7.3 | Post-flip audit-hook | flip on | `/api/health` continues to return clean response | `pytest backend/tests/test_audit_hook_full_t2_pipeline.py::test_audit_hook_emits_zero_warn_under_full_t2_pipeline` (stage 7) | ✅ |

### B8 / B9 — `POST /api/probes` (SSE + 202)

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| B8.1 | Rate limit (5/min per spec §4 — `PROBE_RATE_LIMIT`) | 10 rapid POSTs | first 5 succeed, remainder 429 | Existing rate-limit test in `test_probe_router.py` | ✅ |
| B8.2 | LLM provider unavailable | manual: kill provider mid-Phase-2 | `ProbeGenerationError` → `probe_failed` event with reason | manual SRE drill | 🧪 |
| B8.3 | Caller disconnects mid-SSE | client closes connection mid-stream | Backend coroutine cancels cleanly, RunRow reaches terminal via shield | Cycle 8 Test 4 (spawned task survives caller close) | ✅ |
| B8.4 | Concurrent probes from 3 clients | 3 simultaneous POSTs | All 3 complete, distinct run_ids | manual SRE drill (WriteQueue serializes downstream) | 🧪 |
| B9.1 | Rate limit on `Prefer: respond-async` path | 10 rapid POSTs | same 5/min limit (header doesn't bypass) | manual; same rate-limit decorator | 🧪 |
| B9.2 | `Prefer: respond-async` + LLM unavailable | provider down + 202 dispatch | RunRow → `status='failed'`, polling sees terminal | manual SRE drill | 🧪 |
| B9.3 | Cancellation via abort | `dispatch_async` returns 202, client cancels poll | RunRow reaches terminal via `asyncio.shield(_mark_failed)` | `pytest backend/tests/test_probes_202_polling.py::test_dispatch_async_cancellation_marks_failed_via_shield` | ✅ |
| B9.4 | Concurrent 202 callers | race-stress 50 iterations | 100% find row on immediate GET | `pytest backend/tests/test_probes_202_polling.py::test_post_probes_with_prefer_respond_async_initial_insert_awaited_before_response` | ✅ |

### M2 — `synthesis_replay_suite` MCP tool

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| M2.1 | Tool call with bad suite_id | `suite_id="nonexistent"` | `ValueError("suite_not_found")` propagates as MCP error | `pytest backend/tests/test_mcp_tools_save_replay.py::test_synthesis_replay_suite_*` | ✅ |
| M2.2 | Tool call against retired suite | retire then replay | `ValueError("suite_retired")` | `pytest backend/tests/test_mcp_tools_save_replay.py::test_synthesis_replay_suite_returns_suite_retired_error_envelope_on_retired` | ✅ |
| M2.3 | MCP-process orchestrator missing `replay_run` generator | (Cycle 10 OPERATE finding — fixed at `0a65c28a`) | `replay_run` registered in both backend AND MCP-process orchestrator dicts; `dispatch_async(mode='replay_run')` succeeds | Manual: invoke MCP tool from real client (VS Code Claude Code); confirm via `data/mcp.log` no `unknown mode: replay_run` errors | ✅ |
| M2.4 | 3 concurrent MCP clients | 3 simultaneous `synthesis_replay_suite` calls | All 3 produce distinct run_ids, all 3 `replay_run_persist` op-labels fire | Cycle 10 OPERATE Scenario A custom harness | 🧪 |
| M2.5 | Cross-process MCP→backend bridge | MCP process writes RunRow, backend health endpoint reflects | Both processes share `RunRow` storage via SQLite | Implicit: backend lifespan + MCP lifespan both register orchestrator; covered by integration tests | ✅ |

### F3-F4 — SuiteDetailView Replay + Retire buttons

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| F3.1 | Click Replay | live workbench, suite open | POST `/api/suites/{id}/replay` → toast success → list refresh | manual operator E2E | 🧪 |
| F3.2 | Replay backend 5xx | mock backend 5xx | toast.error fires with message | manual operator + Vitest fixture | 🧪 |
| F3.3 | Replay returns 409 (suite_retired) | retire suite + click Replay | toast.error with "Suite retired" | manual operator | 🧪 |
| F3.4 | Replay button hidden on retired suite | open retired suite | Replay button not rendered | static review + manual | ✅ |
| F3.5 | Replay button keyboard accessibility | Tab + Enter | button focusable, action fires on Enter | manual a11y check | 🧪 |
| F4.1 | Click Retire → modal | live workbench | DestructiveConfirmModal opens with reason input | manual operator E2E | ✅ |
| F4.2 | Retire backend 5xx | mock 5xx | toast.error fires | manual + Vitest | 🧪 |
| F4.3 | Retire returns 404 suite_not_found | bad suite_id | toast.error fires with message | manual operator | 🧪 |
| F4.4 | Retire button hidden when retired_at != null | open retired suite | button not rendered | static review + manual | ✅ |
| F4.5 | `RETIRE` literal-gate confirmation | Type wrong literal, click Retire | Retire button stays disabled until correct literal | DestructiveConfirmModal canonical behavior + manual | ✅ |

### F6 — SeedModal 3rd tab Topic Probe flow

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| F6.1 | Submit valid form | TopicProbeForm valid + click Run | `probesStore.runProbe()` starts; state → `running` → `TopicProbeProgressView` | manual operator E2E | 🧪 |
| F6.2 | Backend 5xx on POST /api/probes | mock 5xx | state → `failed`, error banner + "Run another" button | manual + Vitest | 🧪 |
| F6.3 | Probe completes | `probe_completed` event | state → `completed`, `TopicProbeReportCard` renders with full RunResult | manual operator + `pytest backend/tests/test_topic_only_mode.py` | ✅ |
| F6.4 | Form a11y | keyboard tab through form | All fields focusable, `:focus-visible` outline visible | Cycle 14 a11y audit | ✅ |
| F6.5 | Reduced-motion | `prefers-reduced-motion: reduce` set | Forge-spark animation collapses to 0.01ms | global `app.css` universal rule | ✅ |

### R1-R2 — In-flight cancellation, restart, concurrent writes

| Test ID | Action | Trigger | Expected | Verification | Status |
|---|---|---|---|---|---|
| R1.5 | Orphan `topic_probe` recovery | spawn probe, kill backend mid-flight, restart | `_gc_orphan_runs` sweeps row to `status='failed'` within 1h TTL | `pytest backend/tests/test_gc.py` (existing P3 GC tests) | ✅ |
| R1.6 | Live mid-probe restart | live: probe running, `./init.sh restart` | Row remains `status='running'` until GC; new probe starts cleanly | manual SRE drill | 🧪 |
| R2.5 | Orphan `replay_run` recovery | same as R1.5 but for replay | `_gc_orphan_runs` covers `mode='replay_run'` (verified Cycle 5 OPERATE Scenario E) | static + manual | ✅ |
| R2.6 | Mid-replay restart | replay running, restart | Same as R1.6 — GC-driven recovery | manual SRE drill | 🧪 |
| R4.2 | Concurrent retire on same suite | 5 concurrent `retire()` calls on active suite | 5 events emit (one per call) — **documented divergence from canonical "fires once on state-change"**; spec §9 silent on concurrent semantics. Resolution path: Cycle 3 OPERATE Scenario F + spec follow-up | Cycle 3 OPERATE Scenario F custom harness | 🟡 documented |

---

## Stage 1 — Backend unit + integration tests

Run from `backend/`:

```bash
cd backend && source .venv/bin/activate
pytest tests/test_validation_suite_migration.py tests/test_validation_suite_service.py tests/test_routers_suites.py tests/test_replay_run_generator.py tests/test_topic_only_mode.py tests/test_probes_202_polling.py tests/test_health_regression_alarm.py tests/test_mcp_tools_save_replay.py tests/test_audit_hook_full_t2_pipeline.py -v --timeout=120
```

**Expected**: all ~150 T2 Cycle 1-16 tests PASS (zero failures, zero new skips).

```bash
pytest --cov=app -v --timeout=120
```

**Expected**: full backend suite PASS + ≥90% line coverage (per spec §13.3).

```bash
alembic check
```

**Expected**: exit 0 + `No new upgrade operations detected.`

```bash
ruff check app/ tests/
```

**Expected**: `All checks passed!`

---

## Stage 2 — Frontend unit + integration tests

Run from `frontend/`:

```bash
cd frontend
npm test -- --run
```

**Expected**: 1901/1901 PASS, 0 failures.

```bash
npm run check
```

**Expected**: `0 errors, 0 warnings`.

```bash
npm run test:coverage
```

**Expected**: line coverage ≥78% (per amended spec §13.3 → tracked back to canonical project intent post-v0.4.19).

---

## Stage 3 — Brand + a11y audit (Cycle 14 re-verify)

```bash
# Sweep 1: banned box-shadow blur/spread
grep -rE "box-shadow:.*[1-9]px [1-9]" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/

# Sweep 2: text-shadow + drop-shadow
grep -rE "text-shadow|filter:[[:space:]]*drop-shadow" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/

# Sweep 3: banned standalone-word vocab (hyphenated canon permitted)
grep -rE "(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/
```

**Expected**: zero hits across all 3 sweeps.

```bash
grep -rn ":focus-visible" frontend/src/lib/components/{probes,suites}/
```

**Expected**: focus-visible coverage on every interactive element.

```bash
grep -n "aria-live\|role=" frontend/src/lib/components/suites/RegressionBadge.svelte
```

**Expected**: `role="status"` + `aria-live="polite"` present.

---

## Stage 4 — Migration round-trip

```bash
cd backend && source .venv/bin/activate

# Fresh-DB upgrade
DATABASE_URL="sqlite+aiosqlite:///$(mktemp -t synth_test.XXXXXX.db)" alembic upgrade head

# Downgrade-upgrade round-trip
alembic downgrade -1
alembic upgrade head

# Drift check
alembic check
```

**Expected**: clean upgrade + clean round-trip + zero drift.

---

## Stage 5 — Full-stack E2E flow

**Pre-condition**: `./init.sh restart` brings up all 3 services (backend 8000, MCP 8001, frontend 5199). 

```bash
./init.sh restart
sleep 5  # let lifespan settle
./init.sh status
tail -50 data/backend.log  # confirm zero audit-hook WARN at startup
```

**Then walk this flow** (manual operator):

1. Open frontend at `http://localhost:5199/app`
2. Click **TAXONOMY** activity-bar tab → click **Seed** in toolbar → SeedModal opens → click **TOPIC PROBE** tab (3rd tab)
3. Fill TopicProbeForm: topic ≥3 chars, N=5, intent=explore, grounding=codebase (or topic_only if no repo linked)
4. Click **Run probe** button on form
5. Verify SeedModal body transitions: `idle` → `running` (TopicProbeProgressView with per-prompt strip filling) → `completed` (TopicProbeReportCard renders with top-3 + scores + taxonomy delta + follow-ups)
6. Click **Save as Suite** in report card → enter label `e2e-test-suite` → tolerance 0.5 → confirm
7. Verify toast.success "Suite saved"
8. Click **SUITES** activity-bar tab (6th tab) → SuitesPanel renders with new suite row
9. Click suite row → SuiteDetailView opens → header shows suite metadata + meta-row chips + per-prompt baseline table
10. Click **Replay** button → toast.success "Replay started" → wait for replay to complete (poll `/api/runs/{run_id}` or wait ~30s)
11. Refresh SuiteDetailView → "Replay history" table now shows 1 row with `status='completed'`
12. Open StatusBar → verify RegressionBadge text matches `regression_alarm` state (likely `1 ok` since this replay should match baseline)
13. **Manual scoring degradation test** (per spec §13.4):
    - Edit `backend/app/services/score_blender.py` or `pipeline_phases.py` to artificially lower scores by 1pt
    - `./init.sh restart`
    - Click **Replay** again on the same suite
    - Wait for completion
    - Refresh `/api/health` → verify `regression_alarm.suites_in_alarm >= 1` and the alarm entry includes this suite with `delta_abs <= -1.0`
    - Verify StatusBar RegressionBadge transitions `1 ok → 1 alarm` with `forge-spark` animation
14. **Manual revert test**:
    - Revert the scoring change
    - `./init.sh restart`
    - Click **Replay** again
    - Wait for completion
    - Refresh `/api/health` → verify `regression_alarm.suites_in_alarm == 0` (alarm clears)
    - StatusBar RegressionBadge returns to `1 ok`
15. Click **Retire** button on suite → DestructiveConfirmModal opens
16. Type `RETIRE` literal + enter reason `e2e-test-cleanup` → confirm
17. Verify toast.success "Suite retired" → SuiteDetailView meta-row shows retired_at + retired_reason → Replay button hidden
18. Refresh SuitesPanel → suite row shows `data-retired` dimming

**Expected**: every step succeeds without UI errors, browser console clean, `data/backend.log` shows zero audit-hook WARN lines, `data/frontend.log` shows zero unhandled rejections.

---

## Stage 6 — MCP round-trip (VS Code Claude Code or CLI)

From a real MCP client:

```
synthesis_probe topic="embedding cache invalidation" n_prompts=5
# wait for completion
synthesis_save_suite run_id=<from-previous> label="mcp-e2e-suite" tolerance_abs=0.5
# verify SaveSuiteOutput returned with suite_id
synthesis_replay_suite suite_id=<from-previous>
# verify ReplayInitiatedOutput returned with run_id + poll_url
# wait, then verify via REST GET /api/runs/{run_id}
```

**Expected**: All 3 MCP tools return valid Pydantic structured output. Error envelopes propagate cleanly (test by passing bad inputs).

---

## Stage 7 — Backward compatibility smoke

```bash
# T1 probe via POST /api/probes WITHOUT Prefer header (default SSE)
curl -N -X POST http://localhost:8000/api/probes \
  -H "Content-Type: application/json" \
  -d '{"topic": "compat test", "n_prompts": 5}'
```

**Expected**: SSE response stream identical to v0.4.21 (test by diffing event ordering against a v0.4.21 reference capture).

```bash
# Existing seed batch
curl -X POST http://localhost:8000/api/seed \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "diverse_prompts", "n_prompts": 3}'
```

**Expected**: SeedOutput shape unchanged + additive `run_id` field (P3 contract).

```bash
# GET /api/runs?mode=replay_run (NEW Literal value)
curl http://localhost:8000/api/runs?mode=replay_run
```

**Expected**: 200 + paginated `RunListResponse` (NOT 422 on the new Literal value).

---

## Stage 8 — Audit-hook flip verification (post-soak)

**Pre-condition**: SG-2026-05-11 soak gate CLOSED with 🟢 PASSED status (see [`SOAK_GATES.md`](SOAK_GATES.md)).

```bash
# Confirm flipped default
cd backend && source .venv/bin/activate
python -c "from app.config import Settings; s = Settings(); print(f'WRITE_QUEUE_AUDIT_HOOK_RAISE={s.WRITE_QUEUE_AUDIT_HOOK_RAISE}')"
# Expected: WRITE_QUEUE_AUDIT_HOOK_RAISE=True

# Run integration test under flipped default
pytest tests/test_audit_hook_full_t2_pipeline.py -v
# Expected: 3/3 PASS

# Re-run P4 precondition test
pytest tests/test_tools_optimize.py::test_audit_hook_emits_zero_warn_under_full_pipeline -v
# Expected: PASS

# Kill-switch verification
WRITE_QUEUE_AUDIT_HOOK_RAISE=false ./init.sh restart
grep -E "audit_drift|read-engine audit:" data/backend.log | tail -20
# Expected: WARN behavior re-enabled (kill-switch works)

# Revert to flipped default
unset WRITE_QUEUE_AUDIT_HOOK_RAISE
./init.sh restart
```

---

## Pre-merge checklist (release operator)

Run this once before clicking "merge" on the PR:

- [ ] **SG-2026-05-11 soak gate**: 🟢 PASSED status confirmed in [`SOAK_GATES.md`](SOAK_GATES.md)
- [ ] **Stage 1**: backend unit + integration tests PASS (1 cmd)
- [ ] **Stage 2**: frontend unit + integration tests PASS (1 cmd)
- [ ] **Stage 3**: brand audit 3 grep sweeps zero hits + a11y audit re-verify
- [ ] **Stage 4**: migration round-trip clean + `alembic check` exit 0
- [ ] **Stage 5**: full-stack E2E walkthrough completed (steps 1-18 above)
- [ ] **Stage 6**: MCP round-trip from a real client
- [ ] **Stage 7**: backward-compat smoke (SSE/seed/Literal extension)
- [ ] **Stage 8**: audit-hook flip verification (kill-switch confirmed)
- [ ] `./init.sh restart` clean post-restart log (5-min settle)
- [ ] `docs/CHANGELOG.md` has v0.4.22 section under `## Unreleased`
- [ ] `docs/SHIPPED.md` has `### v0.4.22` section
- [ ] `.github/PR_DESCRIPTION_v0.4.22.md` accurate + filled
- [ ] PR body links to `docs/SOAK_GATES.md` SG-2026-05-11 anchor

---

## Post-release smoke (T+0 to T+24h)

After `./scripts/release.sh` completes:

- [ ] `git tag v0.4.22` exists on origin
- [ ] GitHub Release created with changelog body
- [ ] `version.json` shows `0.4.23-dev` (next dev cycle started)
- [ ] `./init.sh restart` on a clean machine pulls v0.4.22 cleanly
- [ ] Smoke test the Topic Probe end-to-end on a fresh clone
- [ ] Open the workbench, verify RegressionBadge mounts (will be empty `null` since no suites yet — that's correct)
- [ ] Monitor `data/backend.log` for 24h post-ship — confirm zero `read-engine audit:` WARN lines on real production traffic

---

## Reference index

| Resource | Purpose |
|---|---|
| [`docs/SOAK_GATES.md`](SOAK_GATES.md) | Active + historical soak gates, daily check-in tracker |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | Per-change details with file/line refs |
| [`docs/SHIPPED.md`](SHIPPED.md) | Released-feature archive |
| [`docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md`](superpowers/specs/2026-05-11-topic-probe-tier-2-design.md) | T2 spec (14 review rounds APPROVED-ZERO) |
| [`docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`](superpowers/plans/2026-05-11-topic-probe-tier-2.md) | T2 implementation plan (16 cycles) |
| [`.github/PR_DESCRIPTION_v0.4.22.md`](../.github/PR_DESCRIPTION_v0.4.22.md) | PR body draft |
| `backend/tests/test_audit_hook_full_t2_pipeline.py` | T2 audit-hook RAISE coverage (3 tests, `@pytest.mark.integration`) |
| `backend/tests/test_tools_optimize.py::test_audit_hook_emits_zero_warn_under_full_pipeline` | Foundation P4 audit-hook precondition (`@pytest.mark.integration`) |
| `backend/app/config.py` `WRITE_QUEUE_AUDIT_HOOK_RAISE` | Audit-hook flip + kill-switch config field |
| `backend/app/database.py:395-397` | Audit-hook RAISE/WARN implementation |
