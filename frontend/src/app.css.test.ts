// frontend/src/app.css.test.ts
//
// Cycle 11 RED — `forge-spark` keyframe contract.
//
// Spec §6 forge-motion-personality assigns the `forge-spark` one-shot
// animation to two T2 surfaces:
//   - Save-Suite button (Validate stage, post-save flash)
//   - Emergent taxonomy node entry (TaxonomyMiniView)
//
// The keyframe must be defined in a shared stylesheet (canonically the
// shared-keyframes.css single source of truth, imported by app.css), and
// the Save-Suite consumer must declare the animation shorthand. These
// tests use `?raw` imports to inspect the bundled CSS + Svelte component
// source as plain text — matches the existing PatternDensityHeatmap
// audit-lock pattern.
//
// Test 18 imports the not-yet-existing TopicProbeReportCard.svelte
// dynamically so the test runner can still collect + report on test 17
// (which asserts the keyframe in already-existing CSS files).

import { describe, it, expect } from 'vitest';

// `?raw` imports return the literal file contents as a string. Vite +
// Vitest both support this; the test runner sees the file synchronously
// so we get a single static snapshot per test run. These two files
// already exist, so the static imports resolve cleanly.
import appCss from './app.css?raw';
import sharedKeyframesCss from '$lib/styles/shared-keyframes.css?raw';

describe('forge-spark keyframe + consumer wiring', () => {
  // ── Test 17: @keyframes forge-spark defined ─────────────────────────
  //
  // The canonical home for shared keyframes is
  // `frontend/src/lib/styles/shared-keyframes.css`, which is imported
  // once by `app.css` (line 7). Either file satisfies the contract —
  // the test checks both so the consuming components can reference
  // `forge-spark` from any Svelte file without re-declaring it.
  it('test_forge_spark_keyframes_defined_in_app_css', () => {
    const combined = `${appCss}\n${sharedKeyframesCss}`;

    // The block must declare `@keyframes forge-spark` (or `@-webkit-`
    // prefixed). Per brand guidelines `SKILL.md:327`, the signature is
    // "yellow flash + scale(1.2) + rotation".
    expect(combined).toMatch(/@(?:-webkit-)?keyframes\s+forge-spark\s*\{/);

    // The keyframe block must declare at least three frame stops so
    // the flash + scale + rotation arc reads as a single arc rather
    // than a from/to fade. Spec § 6 forge-motion-personality table
    // pins 0% / 25% / 100% as the canonical anchor stops.
    const blockMatch = combined.match(/@keyframes\s+forge-spark\s*\{([\s\S]*?)\n\}/);
    const block = blockMatch?.[1] ?? '';

    expect(block).toMatch(/0%/);
    expect(block).toMatch(/25%/);
    expect(block).toMatch(/100%/);
  });

  // ── Test 18: Save-Suite button consumes forge-spark ─────────────────
  //
  // The TopicProbeReportCard hosts the Save-Suite button. After the user
  // clicks Save the button gets a one-shot `forge-spark 250ms ease-out`
  // animation (spec § 6 forge motion personality table — "Save-as-Suite
  // (click → toast) | Validate | forge-spark one-shot on button …").
  //
  // We use a dynamic `?raw` import so test 17 can still be collected
  // even when the component file doesn't exist yet (RED state).
  it('test_save_suite_button_consumes_forge_spark_animation', async () => {
    // Runtime-computed path so Vite's static analyzer can't resolve it
    // at suite-collection time — the file doesn't exist in RED state.
    const path = [
      '$lib',
      'components',
      'probes',
      'TopicProbeReportCard.svelte?raw',
    ].join('/');
    // @ts-expect-error — `?raw` typing isn't surfaced in the lib types
    const mod = await import(/* @vite-ignore */ path);
    const topicProbeReportCardSource: string = mod.default;

    // The component source must reference `forge-spark` in its CSS so
    // the button picks up the animation shorthand. The exact shorthand
    // form (inline style binding vs class) is implementation-private,
    // but the keyword `forge-spark` must appear in the file.
    expect(topicProbeReportCardSource).toMatch(/forge-spark/);

    // Per spec the binding ships as `forge-spark 250ms ease-out`. Allow
    // either an inline-style binding or a static CSS rule — both forms
    // are valid GREEN landings.
    expect(topicProbeReportCardSource).toMatch(
      /forge-spark\s+250ms\s+ease-out/,
    );
  });
});
