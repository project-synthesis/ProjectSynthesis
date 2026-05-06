# v0.4.17 P2 — Pre-cycle baseline (2026-05-06)

## Baseline SHA
`08655abc0be46b1e4bb24a355a039df7b5648d2a` (referenced by Tasks 5, 7, V2 review)

## Backend test count
3552/3554 tests collected (2 deselected for pre-existing lifespan flakies)

## probe_service.py size
2493 LOC

## External import sites (live grep audit per spec § 3.4)
```
ProbeService
current_probe_id  # noqa: F401
```
Exactly 2 symbols. Spec § 7 inventory matches.

## ProbeService import sites (6)
- app/dependencies/probes.py:26
- app/tools/probe.py:38
- app/routers/probes.py:41
- tests/test_probe_service.py:21
- tests/test_probe_service_queue_routing.py:29
- tests/test_probe_event_correlation.py:101

## current_probe_id import sites (1)
- app/services/probe_event_correlation.py:8

## Test patch-target audit (spec § 4.4 OPERATE check #6)
4 expected lines:
- tests/test_probe_service.py:1039 (alias declaration)
- tests/test_probe_service.py:1043 (patch.object first arg)
- tests/test_probe_service.py:1099 (alias declaration)
- tests/test_probe_service.py:1101 (monkeypatch.setattr first arg)

## Module-level definitions in probe_service.py
9 free functions + 1 ContextVar + 1 class:
- current_probe_id (ContextVar) line 67
- _apply_scope_filter line 77
- _truncate line 88
- _resolve_followups line 92
- _render_final_report line 138
- _commit_with_retry (async) line 256
- _stub_dimension_scores line 306
- _resolve_curated_files line 326
- _resolve_curated_synthesis line 345
- _resolve_dominant_stack line 362
- class ProbeService line 381

## Per-file probe test counts (collected)
- tests/test_probe_agent_template.py: 6 tests collected
- tests/test_probe_cli_shim.py: 3 tests collected
- tests/test_probe_event_correlation.py: 4 tests collected
- tests/test_probe_generation.py: 6 tests collected
- tests/test_probe_mcp_tool.py: 5 tests collected
- tests/test_probe_router.py: 7 tests collected
- tests/test_probe_run_model.py: 4 tests collected
- tests/test_probe_service.py: 13 tests collected
- tests/test_probe_service_queue_routing.py: 30 tests collected

Total: 78 probe tests.
