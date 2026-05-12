# v0.4.22 — Topic Probe Tier 2 + audit-hook WARN→RAISE flip

Ships save-as-suite + replay + regression alarm + 202+polling + topic-only mode + audit-hook flip on top of the Foundation P3 `RunRow` substrate. The first user-driven validation-loop feature in Project Synthesis: frozen `ValidationSuite` snapshots fork from completed `topic_probe` runs and replay deterministically on the same prompt fixture, comparing dimension-scored outputs against baseline and signalling regression at `/api/health.regression_alarm`.

## Summary

- **16 cycles** (14 protocol + 2 gate) — all RED → GREEN → REFACTOR → INTEGRATE → OPERATE + V1 (spec compliance) + V2 (code quality), zero-inconsistency gating on both validators per cycle.
- **55 commits** on `worktree-feature+probe-tier-2` branch.
- **~150 backend Cycle 1-16 tests** + **1901 frontend tests** all PASS. ruff + svelte-check + alembic check all clean.
- **Full-stack engineer pre-PR audit** caught + fixed 1 critical UI wiring blocker (SeedModal 3rd tab was a stub) + 2 follow-up recs (SuiteDetailView Replay/Retire buttons).

## What ships

- **Backend** (Cycles 1-10, 15): atomic migration `5576c539720f` (`validation_suite` table + FK + indexes), `ValidationSuiteService` (6 methods, detached-ORM-safe), `routers/suites.py` (6 endpoints + rate limits + 10-code error envelope), `ReplayRunGenerator` + `_aggregate.py` shared helper, `RunOrchestrator.dispatch_async()` + `done_event`/`_done_future` dual-channel signaling, topic-only mode + 2nd template, `/api/health.regression_alarm` block, 2 new MCP tools (15→17), `WRITE_QUEUE_AUDIT_HOOK_RAISE` flipped to `True` with env-var kill-switch.
- **Frontend** (Cycles 11-13): 4 NEW probes components, 4 NEW suites components, `RegressionBadge` in `StatusBar`, 6th `SUITES` ActivityBar tab, `suites.svelte.ts` + `probes.svelte.ts` Svelte 5 runes stores, `probesStore.runProbe()` async-iterable abstraction (auto-attaches `Prefer: respond-async` for `n_prompts > 10`).
- **Doc-sync**: SKILL.md 5→7-dir scope + 22px→20px StatusBar, component-patterns.md 600ms→1500ms `copy-flash` + 250ms `forge-spark`, backend/CLAUDE.md + root CLAUDE.md probe event count 7→6 (`probe_taxonomy_change` was never emitted), ROADMAP.md path placement + event correction.

## Backward compat

- `POST /api/probes` without `Prefer:` header returns SSE response identical to v0.4.21. T1 probes work end-to-end with zero behavior change.
- Existing seed agent flows + `synthesis_probe`/`synthesis_seed` MCP tools unchanged.
- `GET /api/runs?mode=...` accepts new `replay_run` Literal (additive).
- Audit-hook flip preserves v0.4.21 behavior for callers that opt out via env `WRITE_QUEUE_AUDIT_HOOK_RAISE=false`.

## Test plan

- [ ] Soak verification ≥2026-05-18: `grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100` returns zero unexpected sources from the v0.4.21 soak window.
- [ ] Manual E2E (per spec §13 success criterion 4): probe → save-as-suite → degrade scoring → replay → `/api/health.regression_alarm.suites_in_alarm >= 1` → revert → replay → alarm clears.
- [ ] `./init.sh restart` smoke — all 3 services green, no `audit_drift` / `read-engine audit:` WARN lines post-restart.
- [ ] MCP `synthesis_save_suite` + `synthesis_replay_suite` round-trip from VS Code Claude Code session.
- [ ] Frontend: SeedModal 3rd tab full 3-state workflow (idle → form → progress → report) reachable; SuiteDetailView Replay + Retire actions work; RegressionBadge updates on state transitions.

## Soak gate

This PR is gated on the 7-day post-v0.4.21 soak window closing at **≥2026-05-18** before merge + release. The audit-hook WARN→RAISE flip (Cycle 15) requires production soak confirmation that all P4 long-handler restructures route writes through `WriteQueue.submit()` cleanly.

## References

- **Spec**: `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` (14 validation rounds, APPROVED-ZERO at `9b843791`)
- **Plan**: `docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md` (16 cycles, V2 APPROVED-ZERO at `91655a25`)
- **Migration**: `5576c539720f_validation_suite_topic_probe_t2.py` (atomic forward-only with idempotency guards + reversible downgrade)
- **Foundation P3** (substrate this builds on): v0.4.18 `RunRow`
- **Foundation P4** (audit-hook flip precondition): v0.4.21 long-handler restructures

🤖 Generated with [Claude Code](https://claude.com/claude-code)
