import { describe, it, expect } from 'vitest';
import { navSlide, navFade, easeSpring } from './transitions';

describe('navSlide / navFade presets', () => {
  it('navSlide has 180ms duration and easeSpring easing', () => {
    expect(navSlide.duration).toBe(180);
    expect(navSlide.easing).toBe(easeSpring);
  });

  it('navFade has 120ms duration and easeSpring easing', () => {
    expect(navFade.duration).toBe(120);
    expect(navFade.easing).toBe(easeSpring);
  });
});

describe('easeSpring bezier solver', () => {
  it('anchors at 0 and 1', () => {
    expect(easeSpring(0)).toBe(0);
    expect(easeSpring(1)).toBe(1);
  });

  it('clamps values below 0 and above 1', () => {
    expect(easeSpring(-0.1)).toBe(0);
    expect(easeSpring(1.5)).toBe(1);
  });

  it('produces monotonically increasing output', () => {
    const samples = [0.1, 0.25, 0.5, 0.75, 0.9].map(easeSpring);
    for (let i = 1; i < samples.length; i++) {
      expect(samples[i]).toBeGreaterThan(samples[i - 1]);
    }
  });

  it('stays within [0, 1] for all interior samples', () => {
    for (let t = 0; t <= 1; t += 0.05) {
      const y = easeSpring(t);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(y).toBeLessThanOrEqual(1);
    }
  });

  it('matches cubic-bezier(0.16, 1, 0.3, 1) — ease-out with fast ramp', () => {
    // ease-spring is a strong ease-out: y(0.25) should already be >0.6,
    // distinctly faster than Svelte's cubicOut (~0.58 at t=0.25).
    expect(easeSpring(0.25)).toBeGreaterThan(0.6);
    // And the mid-point is well past the linear midline.
    expect(easeSpring(0.5)).toBeGreaterThan(0.85);
  });
});
