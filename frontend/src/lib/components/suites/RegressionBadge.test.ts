// frontend/src/lib/components/suites/RegressionBadge.test.ts
//
// Cycle 12 RED — RegressionBadge contract.
//
// The badge mounts inside `StatusBar.svelte` (right cluster, alongside the
// `domains` / `clusters` / `Q` / `UpdateBadge` / `SSE` items). It surfaces
// the count of suites in regression alarm, chromatic-encoded:
//
//   - Nominal — neon-green `#22ff88` text, copy `"<N> ok"`
//   - Firing  — neon-red `#ff3366` text, copy `"<N> alarm"`
//
// Voice (spec § 6 voice table):
//   - Lower-case noun (matches shipped `statusLabel()` lower-case pattern
//     in `SeedModal.svelte:227-233` — "the voice is technical/precise
//     rather than shouting all-caps"). The chromatic color carries the
//     urgency signal, not the case.
//
// Density (spec § 6 density-pins table):
//   - `px-1.5 py-0` — edge-to-edge inside the 20px status-bar. Matches
//     `TierBadge`/`UpdateBadge`/`ProviderBadge` precedent.
//   - `text-[10px]` `font-mono`
//
// Motion (spec § 6 forge motion personality):
//   - Nominal → Firing transition fires `forge-spark` ONCE on the badge
//     (250ms ease-out), then settles to static red. The keyframe is
//     defined in `lib/styles/shared-keyframes.css` and pinned by
//     `app.css.test.ts:test_forge_spark_keyframes_defined_in_app_css`.
//
// All assertions are behavior-level; brand-canon CSS audits live in the
// cycle-14 audit grep, not in these tests.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render } from '@testing-library/svelte';

const suitesStoreMock = vi.hoisted(() => ({
  suitesStore: {
    regressionAlarmBlock: null as Record<string, unknown> | null,
    refresh: vi.fn(),
  },
}));
vi.mock('$lib/stores/suites.svelte', () => suitesStoreMock);

async function loadComponent() {
  // Runtime-computed path — keeps Vite's static analyzer from resolving
  // the file (which doesn't exist in RED state) at suite collection time.
  const path = ['.', 'RegressionBadge.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

function makeAlarmBlock(overrides: Record<string, unknown> = {}) {
  return {
    suites_total: 12,
    suites_in_alarm: 0,
    latest_alarms: [] as unknown[],
    ...overrides,
  };
}

describe('RegressionBadge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    suitesStoreMock.suitesStore.regressionAlarmBlock = null;
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 9: nominal lower-case "12 ok" ──────────────────────────────
  //
  // When `suites_in_alarm === 0` AND `suites_total > 0` the badge reads
  // `"<suites_total> ok"`. Lower-case noun, no period — matches the
  // canonical badge convention pinned by spec § 6 voice table.
  //
  // Per `SeedModal.svelte:227-233` shipped pattern the case is technical/
  // precise rather than shouting all-caps; the chromatic green carries
  // the "everything healthy" signal, not capitalised emphasis.
  it('test_regression_badge_nominal_lower_case_12_ok', async () => {
    suitesStoreMock.suitesStore.regressionAlarmBlock = makeAlarmBlock({
      suites_total: 12,
      suites_in_alarm: 0,
    });

    const RegressionBadge = await loadComponent();
    const { container } = render(RegressionBadge);

    // The badge text content matches `"12 ok"` exactly (modulo whitespace).
    // Reject any UPPERCASE variant — the voice contract is lower-case.
    const txt = (container.textContent ?? '').trim();
    expect(txt).toMatch(/\b12 ok\b/);
    expect(txt).not.toMatch(/\b12 OK\b/);
    expect(txt).not.toMatch(/\bOK\b/); // catches OK / Ok variants
  });

  // ── Test 10: firing lower-case "2 firing" ───────────────────────────
  //
  // When `suites_in_alarm > 0` the badge reads `"<suites_in_alarm> firing"`
  // (active verb — the chromatic red is the load-bearing urgency signal).
  // Per spec § 6 voice table + R-20/R-26 (v0.4.39): instrument-voice
  // prefers the active-state verb `firing` over the noun `alarm` — the
  // suite is firing a regression alert, not "in alarm". Lower-case.
  it('test_regression_badge_firing_lower_case_2_firing', async () => {
    suitesStoreMock.suitesStore.regressionAlarmBlock = makeAlarmBlock({
      suites_total: 12,
      suites_in_alarm: 2,
      latest_alarms: [
        { suite_id: 'a', label: 'l1' },
        { suite_id: 'b', label: 'l2' },
      ],
    });

    const RegressionBadge = await loadComponent();
    const { container } = render(RegressionBadge);

    const txt = (container.textContent ?? '').trim();
    expect(txt).toMatch(/\b2 firing\b/);
    expect(txt).not.toMatch(/\bFIRING\b/);
    expect(txt).not.toMatch(/\bFiring\b/);
    // Vocab change pinned — `alarm` voice retired in v0.4.39.
    expect(txt).not.toMatch(/\balarm\b/i);
  });

  // ── Test 11: edge-to-edge inside the 20px status bar ────────────────
  //
  // Density pin (spec § 6 density-pins table): RegressionBadge uses
  //   `px-1.5 py-0` — edge-to-edge inside the bar height
  // Matches `TierBadge` / `UpdateBadge` / `ProviderBadge` precedent.
  //
  // The shipped bar is 20px tall (`StatusBar.svelte:251`); the badge
  // fills the bar regardless of the canon 22px → shipped 20px doc-sync
  // pending in spec § 11 item 11.
  //
  // We inspect the component source for the canonical Tailwind utility
  // OR CSS-rule equivalent — both forms are valid GREEN landings.
  it('test_regression_badge_edge_to_edge_inside_status_bar', async () => {
    // `?raw` import — file doesn't exist in RED, runtime-computed path
    // defers static analysis until test run.
    const path = ['.', 'RegressionBadge.svelte?raw'].join('/');
    const mod = await import(/* @vite-ignore */ path);
    const src: string = mod.default;

    // px-1.5 — Tailwind utility OR CSS `padding-inline: 6px` / `padding-
    // left+right: 6px` (each is valid).
    expect(src).toMatch(/px-1\.5|padding-inline:\s*6px|padding:\s*0\s+6px/);

    // py-0 — Tailwind utility OR CSS `padding-block: 0` / `padding-top:0;
    // padding-bottom:0`.
    expect(src).toMatch(/py-0|padding-block:\s*0|padding-top:\s*0/);

    // text-[10px] — the StatusBar canonical font size. Either the Tailwind
    // arbitrary or a CSS rule binding.
    expect(src).toMatch(/text-\[10px\]|font-size:\s*10px/);

    // font-mono — the StatusBar canonical typography. Either the Tailwind
    // utility or the CSS var ref.
    expect(src).toMatch(/font-mono|var\(--font-mono\)/);
  });

  // ── Test 12: nominal→firing transition fires forge-spark 250ms once ──
  //
  // Spec § 6 forge motion personality:
  //   "Regression nominal → firing | Validate | forge-spark on StatusBar
  //    badge once, then static red"
  //
  // The `forge-spark` keyframe is defined in
  // `lib/styles/shared-keyframes.css` (pinned by app.css.test.ts:17). The
  // badge consumer must declare the animation shorthand `forge-spark
  // 250ms ease-out` (one-shot, not infinite).
  //
  // We verify by inspecting the component source for the animation
  // shorthand — matches the same audit-lock pattern used by Cycle 11's
  // `test_save_suite_button_consumes_forge_spark_animation` (app.css
  // .test.ts:67).
  it('test_regression_badge_nominal_to_firing_transition_fires_forge_spark', async () => {
    const path = ['.', 'RegressionBadge.svelte?raw'].join('/');
    const mod = await import(/* @vite-ignore */ path);
    const src: string = mod.default;

    // The keyword must appear — implementation may bind via class, inline
    // style, or CSS rule.
    expect(src).toMatch(/forge-spark/);

    // The animation shorthand `forge-spark 250ms ease-out` is the spec
    // contract (250ms timing pin per spec § 6 table). Per
    // `shared-keyframes.css:126`: "Consumed via `animation: forge-spark
    // 250ms ease-out` shorthand — the timing pin (250ms ease-out) is
    // part of the spec contract."
    expect(src).toMatch(/forge-spark\s+250ms\s+ease-out/);

    // The animation must NOT be infinite — § 6 personality table
    // specifies "once, then static red". Reject any `infinite` keyword
    // bound to the forge-spark shorthand or a sibling animation rule
    // operating on the same keyframe name. Capture the line/declaration
    // containing `forge-spark` and assert no `infinite` in that span.
    const sparkLines = src
      .split('\n')
      .filter((line) => line.includes('forge-spark'));
    const sparkSpan = sparkLines.join('\n');
    expect(sparkSpan).not.toMatch(/\binfinite\b/);
  });
});
