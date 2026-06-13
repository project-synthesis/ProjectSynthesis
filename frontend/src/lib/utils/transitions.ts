// Single source of truth for the brand motion personality.
//
// Every entrance flows through ``easeSpring`` (matches CSS ``--ease-spring``).
// Every dismissal flows through ``easeExit`` (matches CSS ``--ease-exit``).
// Svelte's stock ``cubicOut`` is materially flatter (cubic-bezier(0.33, 1,
// 0.68, 1)) — drifting from CSS by enough to break the brand "spring-loaded
// motion" cadence on tightly choreographed multi-property transitions.
//
// Newton-Raphson solves each parametric curve so JS-driven transitions
// match the CSS variable EXACTLY. Eight iterations resolve every input
// to <1e-6 in practice; the early-exit fast path handles 0/1 endpoints.

// --- Spring entrance: cubic-bezier(0.16, 1, 0.3, 1) — see app.css:89 ---
{
  // namespaced solver constants so the exit solver below can reuse the
  // pattern without symbol clashes.
}

const SPRING_P1X = 0.16;
const SPRING_P1Y = 1;
const SPRING_P2X = 0.3;
const SPRING_P2Y = 1;

const SPRING_cx = 3 * SPRING_P1X;
const SPRING_bx = 3 * (SPRING_P2X - SPRING_P1X) - SPRING_cx;
const SPRING_ax = 1 - SPRING_cx - SPRING_bx;
const SPRING_cy = 3 * SPRING_P1Y;
const SPRING_by = 3 * (SPRING_P2Y - SPRING_P1Y) - SPRING_cy;
const SPRING_ay = 1 - SPRING_cy - SPRING_by;

const sampleSpringX = (u: number): number =>
  ((SPRING_ax * u + SPRING_bx) * u + SPRING_cx) * u;
const sampleSpringY = (u: number): number =>
  ((SPRING_ay * u + SPRING_by) * u + SPRING_cy) * u;
const sampleSpringDerivX = (u: number): number =>
  (3 * SPRING_ax * u + 2 * SPRING_bx) * u + SPRING_cx;

export function easeSpring(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;

  let u = t;
  for (let i = 0; i < 8; i++) {
    const dx = sampleSpringX(u) - t;
    if (Math.abs(dx) < 1e-6) break;
    const slope = sampleSpringDerivX(u);
    if (Math.abs(slope) < 1e-6) break;
    u -= dx / slope;
  }
  return sampleSpringY(u);
}

// --- Exit easing: cubic-bezier(0.4, 0, 1, 1) — see app.css:90 ---
// Mirrors ``easeSpring``'s structure so JS-driven dismissals follow the
// same accelerating curve as their CSS counterparts. Used by ``dialogOut``
// + ``listRemove`` presets below.

const EXIT_P1X = 0.4;
const EXIT_P1Y = 0;
const EXIT_P2X = 1;
const EXIT_P2Y = 1;

const EXIT_cx = 3 * EXIT_P1X;
const EXIT_bx = 3 * (EXIT_P2X - EXIT_P1X) - EXIT_cx;
const EXIT_ax = 1 - EXIT_cx - EXIT_bx;
const EXIT_cy = 3 * EXIT_P1Y;
const EXIT_by = 3 * (EXIT_P2Y - EXIT_P1Y) - EXIT_cy;
const EXIT_ay = 1 - EXIT_cy - EXIT_by;

const sampleExitX = (u: number): number =>
  ((EXIT_ax * u + EXIT_bx) * u + EXIT_cx) * u;
const sampleExitY = (u: number): number =>
  ((EXIT_ay * u + EXIT_by) * u + EXIT_cy) * u;
const sampleExitDerivX = (u: number): number =>
  (3 * EXIT_ax * u + 2 * EXIT_bx) * u + EXIT_cx;

export function easeExit(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;

  let u = t;
  for (let i = 0; i < 8; i++) {
    const dx = sampleExitX(u) - t;
    if (Math.abs(dx) < 1e-6) break;
    const slope = sampleExitDerivX(u);
    if (Math.abs(slope) < 1e-6) break;
    u -= dx / slope;
  }
  return sampleExitY(u);
}

// ---------------------------------------------------------------------------
// Presets — every Svelte transition site MUST import one of these.
//
// Durations follow the brand tier system (app.css:91-96):
//   --duration-micro:        150ms — micro feedback
//   --duration-hover:        200ms — interactive feedback
//   --duration-structural:   300ms — structural motion
//   --duration-stagger:      350ms — sequential delays
//   --duration-progress:     500ms — progress indicators
//   --duration-skeleton:    1500ms — loading placeholder loop
//
// If a new pattern needs its own preset, add it here — never hand-roll
// `{ duration, easing }` literals at call sites. Keeps the brand motion
// surface tweakable from a single file.
// ---------------------------------------------------------------------------

/** Sidebar slide expand/collapse. 180ms at the spring curve. */
export const navSlide = { duration: 180, easing: easeSpring };

/** Sidebar fade for state-filter transitions. 120ms at the spring curve. */
export const navFade = { duration: 120, easing: easeSpring };

/** Tab-panel cross-fade. Tab switches feel snappy at 120ms with no
 *  perceptible delay; longer durations make the workbench feel sluggish
 *  during repeated tabbing. */
export const tabFade = { duration: 120, easing: easeSpring };

/** Modal scrim + dialog entrance. 200ms at the spring curve — long enough
 *  that the dialog visibly settles, short enough that the user isn't
 *  waiting on chrome before interacting with content. */
export const dialogIn = { duration: 200, easing: easeSpring };

/** Modal scrim + dialog dismissal. 150ms at the exit curve — quicker than
 *  entrance per the brand "decisive lateral snap" cadence on dismissal. */
export const dialogOut = { duration: 150, easing: easeExit };

/** Scrim entrance — alias of dialogIn for surfaces that want to express
 *  "the scrim is fading in" explicitly. The scrim and dialog must use
 *  matched durations so they appear/dismiss in lockstep; deviating
 *  visually announces an out-of-order render. Brand: chrome moves
 *  together. */
export const scrimIn = dialogIn;

/** Scrim dismissal — alias of dialogOut. See ``scrimIn``. */
export const scrimOut = dialogOut;

/** List row insertion (history rows, cluster nodes, refinement turns).
 *  Matches ``navSlide`` so insert/expand transitions in the same panel
 *  feel cohesive. */
export const listInsert = { duration: 180, easing: easeSpring };

/** List row deletion. Mirrors ``dialogOut`` cadence — exits should feel
 *  decisive, not lingering. */
export const listRemove = { duration: 150, easing: easeExit };

/** Pipeline phase reveals (analyze → optimize → score telemetry cards).
 *  300ms at the spring curve — structural tier so phase boundaries read
 *  as deliberate transitions rather than micro-interactions. */
export const phaseReveal = { duration: 300, easing: easeSpring };

/** Status badge label/color cross-fade (TierBadge, StatusBar tier labels).
 *  Matches the global hover duration so badge updates feel like the same
 *  visual class as button hovers. */
export const badgeCrossfade = { duration: 200, easing: easeSpring };
