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
| 2026-05-12 | 1 | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
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
