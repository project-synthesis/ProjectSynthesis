import { describe, it, expect } from 'vitest';
import { scoreColor, taxonomyColor, qHealthColor, stateColor } from './colors';

describe('scoreColor', () => {
  it('returns dim for null', () => {
    expect(scoreColor(null)).toBe('var(--color-text-dim)');
  });

  it('returns dim for 0 (boundary: <= 0)', () => {
    expect(scoreColor(0)).toBe('var(--color-text-dim)');
  });

  it('returns green for 9+', () => {
    expect(scoreColor(9.5)).toBe('var(--color-neon-green)');
  });

  it('returns green at exact boundary 9.0', () => {
    expect(scoreColor(9.0)).toBe('var(--color-neon-green)');
  });

  it('returns cyan for 7-8.9', () => {
    expect(scoreColor(7.5)).toBe('var(--color-neon-cyan)');
  });

  it('returns cyan at exact boundary 7.0', () => {
    expect(scoreColor(7.0)).toBe('var(--color-neon-cyan)');
  });

  it('returns yellow for 4-6.9', () => {
    expect(scoreColor(5.0)).toBe('var(--color-neon-yellow)');
  });

  it('returns yellow at exact boundary 4.0', () => {
    expect(scoreColor(4.0)).toBe('var(--color-neon-yellow)');
  });

  it('returns red for below 4', () => {
    expect(scoreColor(2.0)).toBe('var(--color-neon-red)');
  });
});

describe('taxonomyColor', () => {
  it('returns hex color as-is', () => {
    expect(taxonomyColor('#a855f7')).toBe('#a855f7');
  });

  it('returns fallback for null/undefined', () => {
    expect(taxonomyColor(null)).toBe('#7a7a9e');
    expect(taxonomyColor(undefined)).toBe('#7a7a9e');
  });

  it('resolves known domain names to hex colors', () => {
    expect(taxonomyColor('backend')).toBe('#b44aff');
    expect(taxonomyColor('frontend')).toBe('#ff6a00');
    expect(taxonomyColor('database')).toBe('#00e0d0');
    expect(taxonomyColor('security')).toBe('#ff2255');
    expect(taxonomyColor('devops')).toBe('#3d9eff');
    expect(taxonomyColor('fullstack')).toBe('#d946ef');
    expect(taxonomyColor('general')).toBe('#7a7a9e');
  });

  it('resolves free-form domain strings via keyword matching', () => {
    expect(taxonomyColor('frontend CSS architecture')).toBe('#ff6a00');
    expect(taxonomyColor('backend API service')).toBe('#b44aff');
    expect(taxonomyColor('database optimization')).toBe('#00e0d0');
  });

  it('returns fallback for unknown domain name', () => {
    expect(taxonomyColor('unknown-domain')).toBe('#7a7a9e');
  });

  it('returns fallback for empty string', () => {
    expect(taxonomyColor('')).toBe('#7a7a9e');
  });
});

describe('qHealthColor', () => {
  it('returns dim for null', () => {
    expect(qHealthColor(null)).toBe('var(--color-text-dim)');
  });

  it('returns green for >= 0.8', () => {
    expect(qHealthColor(0.9)).toBe('var(--color-neon-green)');
  });

  it('returns cyan for >= 0.6', () => {
    expect(qHealthColor(0.7)).toBe('var(--color-neon-cyan)');
  });

  it('returns yellow for >= 0.4', () => {
    expect(qHealthColor(0.5)).toBe('var(--color-neon-yellow)');
  });

  it('returns red for < 0.4', () => {
    expect(qHealthColor(0.2)).toBe('var(--color-neon-red)');
  });
});

describe('stateColor', () => {
  it('returns correct color for each lifecycle state', () => {
    expect(stateColor('candidate')).toBe('#7a7a9e');
    expect(stateColor('active')).toBe('#4d8eff');
    expect(stateColor('mature')).toBe('#a855f7');
    expect(stateColor('template')).toBe('#00e5ff');
    expect(stateColor('archived')).toBe('#2a2a3e');
  });

  it('returns fallback gray for unknown state', () => {
    expect(stateColor('nonexistent')).toBe('#7a7a9e');
  });

  it('returns fallback gray for empty string', () => {
    expect(stateColor('')).toBe('#7a7a9e');
  });
});
