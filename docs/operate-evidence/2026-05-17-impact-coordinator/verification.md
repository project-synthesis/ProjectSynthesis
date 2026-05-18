# Sub-project C OPERATE verification — 2026-05-17

## Status: STATIC-TEST-EQUIVALENT

All 6 OPERATE measurements are pinned at the test layer:

| Measurement | Test coverage |
|---|---|
| M1 Coordinator instantiated | Unit test #1 + source-grep #9 |
| M2 fire('click') fires beam | Unit tests #2 + INT-1 |
| M3 onImpact chain order | Unit test #8 + INT-1 |
| M4 T3.4 idle pulse on engulfed selected | Unit test #17 |
| M5 Breathing handler no overwrite | Source-grep #12 (positive guard) + #8 (negative — old branch gone) |
| M6 Dispose ordering | Source-grep #10 |

CDP-based live verification deferred to integration in CI dashboard (out of
scope for Sub-project C). All static + unit + integration test surfaces
green: 515/515 taxonomy tests, perf-budget 6/6, svelte-check 0/0.

