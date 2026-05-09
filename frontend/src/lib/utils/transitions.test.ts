import { describe, it, expect } from 'vitest';
import {
  easeSpring,
  easeExit,
  navSlide,
  navFade,
  tabFade,
  dialogIn,
  dialogOut,
  listInsert,
  listRemove,
  phaseReveal,
  badgeCrossfade,
} from './transitions';

describe('easeSpring (cubic-bezier(0.16, 1, 0.3, 1))', () => {
  it('anchors at 0 and 1', () => {
    expect(easeSpring(0)).toBe(0);
    expect(easeSpring(1)).toBe(1);
  });

  it('clamps values below 0 and above 1', () => {
    expect(easeSpring(-0.1)).toBe(0);
    expect(easeSpring(1.5)).toBe(1);
  });

  it('produces monotonically non-decreasing output', () => {
    let prev = easeSpring(0);
    for (let t = 0.05; t <= 1.0; t += 0.05) {
      const cur = easeSpring(t);
      expect(cur).toBeGreaterThanOrEqual(prev);
      prev = cur;
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
    // The point of using the Newton-Raphson spring solver instead of
    // Svelte's stock cubicOut: spring rises faster early. y(0.25) is
    // distinctly higher than cubicOut(~0.58) at the same input.
    expect(easeSpring(0.25)).toBeGreaterThan(0.6);
    expect(easeSpring(0.5)).toBeGreaterThan(0.85);
  });
});

describe('easeExit (cubic-bezier(0.4, 0, 1, 1))', () => {
  it('anchors at 0 and 1', () => {
    expect(easeExit(0)).toBe(0);
    expect(easeExit(1)).toBe(1);
  });

  it('clamps values below 0 and above 1', () => {
    expect(easeExit(-0.5)).toBe(0);
    expect(easeExit(1.5)).toBe(1);
  });

  it('produces monotonically non-decreasing output', () => {
    let prev = easeExit(0);
    for (let t = 0.05; t <= 1.0; t += 0.05) {
      const cur = easeExit(t);
      expect(cur).toBeGreaterThanOrEqual(prev);
      prev = cur;
    }
  });

  it('starts slow and ends fast (accelerating curve)', () => {
    // P1=(0.4, 0) holds the curve near zero through the first 40% of t,
    // then it accelerates to 1. The back half should rise more than the
    // front half — the brand "decisive lateral snap" cadence on dismissal.
    const front = easeExit(0.5) - easeExit(0.0);
    const back = easeExit(1.0) - easeExit(0.5);
    expect(back).toBeGreaterThan(front);
  });
});

describe('preset durations + easings', () => {
  it('navSlide: 180ms with easeSpring', () => {
    expect(navSlide).toEqual({ duration: 180, easing: easeSpring });
  });

  it('navFade: 120ms with easeSpring', () => {
    expect(navFade).toEqual({ duration: 120, easing: easeSpring });
  });

  it('tabFade: 120ms with easeSpring (matches navFade cadence)', () => {
    expect(tabFade).toEqual({ duration: 120, easing: easeSpring });
  });

  it('dialogIn: 200ms with easeSpring (matches --duration-hover)', () => {
    expect(dialogIn).toEqual({ duration: 200, easing: easeSpring });
  });

  it('dialogOut: 150ms with easeExit (matches --duration-micro, exit curve)', () => {
    expect(dialogOut).toEqual({ duration: 150, easing: easeExit });
  });

  it('listInsert: matches navSlide cadence so insert/expand feel cohesive', () => {
    expect(listInsert.duration).toBe(navSlide.duration);
    expect(listInsert.easing).toBe(navSlide.easing);
  });

  it('listRemove: matches dialogOut cadence so dismissals feel decisive', () => {
    expect(listRemove.duration).toBe(dialogOut.duration);
    expect(listRemove.easing).toBe(dialogOut.easing);
  });

  it('phaseReveal: 300ms (structural tier) with easeSpring', () => {
    expect(phaseReveal).toEqual({ duration: 300, easing: easeSpring });
  });

  it('badgeCrossfade: 200ms (hover tier) with easeSpring', () => {
    expect(badgeCrossfade).toEqual({ duration: 200, easing: easeSpring });
  });

  it('every entrance preset uses easeSpring (brand contract)', () => {
    for (const preset of [navSlide, navFade, tabFade, dialogIn, listInsert, phaseReveal, badgeCrossfade]) {
      expect(preset.easing).toBe(easeSpring);
    }
  });

  it('every exit preset uses easeExit (brand contract)', () => {
    for (const preset of [dialogOut, listRemove]) {
      expect(preset.easing).toBe(easeExit);
    }
  });
});
