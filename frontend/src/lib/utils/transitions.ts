// Single source of truth for --ease-spring (see src/app.css:89).
// Svelte's cubicOut is cubic-bezier(0.33, 1, 0.68, 1) — materially flatter than
// ease-spring. Solve the parametric curve via Newton-Raphson so the JS
// transitions match CSS transitions exactly.
const P1X = 0.16;
const P1Y = 1;
const P2X = 0.3;
const P2Y = 1;

const cx = 3 * P1X;
const bx = 3 * (P2X - P1X) - cx;
const ax = 1 - cx - bx;
const cy = 3 * P1Y;
const by = 3 * (P2Y - P1Y) - cy;
const ay = 1 - cy - by;

const sampleX = (u: number): number => ((ax * u + bx) * u + cx) * u;
const sampleY = (u: number): number => ((ay * u + by) * u + cy) * u;
const sampleDerivX = (u: number): number => (3 * ax * u + 2 * bx) * u + cx;

export function easeSpring(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;

  let u = t;
  for (let i = 0; i < 8; i++) {
    const dx = sampleX(u) - t;
    if (Math.abs(dx) < 1e-6) break;
    const slope = sampleDerivX(u);
    if (Math.abs(slope) < 1e-6) break;
    u -= dx / slope;
  }
  return sampleY(u);
}

// Sidebar sections use these with Svelte's slide/fade transitions.
export const navSlide = { duration: 180, easing: easeSpring };
export const navFade = { duration: 120, easing: easeSpring };
