import { describe, it, expect, beforeEach } from 'vitest';
import { scoreColor, taxonomyColor, qHealthColor, stateColor, HIGHLIGHT_COLOR_HEX, SIMILARITY_EDGE_COLOR_HEX } from './colors';
import { domainStore } from '$lib/stores/domains.svelte';

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
  beforeEach(() => {
    domainStore._reset();
  });

  it('returns hex color as-is', () => {
    expect(taxonomyColor('#a855f7')).toBe('#a855f7');
  });

  it('returns fallback for null/undefined', () => {
    expect(taxonomyColor(null)).toBe('#7a7a9e');
    expect(taxonomyColor(undefined)).toBe('#7a7a9e');
  });

  it('returns fallback for empty string', () => {
    expect(taxonomyColor('')).toBe('#7a7a9e');
  });

  it('returns fallback for unknown domain when store is empty', () => {
    expect(taxonomyColor('backend')).toBe('#7a7a9e');
    expect(taxonomyColor('unknown-domain')).toBe('#7a7a9e');
  });

  it('resolves known domain names when store is populated', () => {
    domainStore.domains = [
      { id: '1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: '2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
      { id: '3', label: 'database', color_hex: '#36b5ff', member_count: 0, avg_score: null, source: 'seed' },
      { id: '4', label: 'general', color_hex: '#7a7a9e', member_count: 0, avg_score: null, source: 'seed' },
    ];
    expect(taxonomyColor('backend')).toBe('#b44aff');
    expect(taxonomyColor('frontend')).toBe('#ff4895');
    expect(taxonomyColor('database')).toBe('#36b5ff');
    expect(taxonomyColor('general')).toBe('#7a7a9e');
  });

  it('resolves free-form domain strings via keyword matching when store is populated', () => {
    domainStore.domains = [
      { id: '1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: '2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
    ];
    expect(taxonomyColor('frontend CSS architecture')).toBe('#ff4895');
    expect(taxonomyColor('backend API service')).toBe('#b44aff');
  });

  it('resolves primary:qualifier format to primary domain color', () => {
    domainStore.domains = [
      { id: '1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: '2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
    ];
    expect(taxonomyColor('backend: security')).toBe('#b44aff');
    expect(taxonomyColor('frontend: accessibility')).toBe('#ff4895');
  });

  it('returns fallback for unrecognized domain even when store is populated', () => {
    domainStore.domains = [
      { id: '1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
    ];
    expect(taxonomyColor('unknown-domain')).toBe('#7a7a9e');
  });
});

describe('qHealthColor', () => {
  it('returns dim for null', () => {
    expect(qHealthColor(null)).toBe('var(--color-text-dim)');
  });

  it('returns green for >= 0.70', () => {
    expect(qHealthColor(0.9)).toBe('var(--color-neon-green)');
    expect(qHealthColor(0.70)).toBe('var(--color-neon-green)');
  });

  it('returns cyan for >= 0.50', () => {
    expect(qHealthColor(0.6)).toBe('var(--color-neon-cyan)');
    expect(qHealthColor(0.50)).toBe('var(--color-neon-cyan)');
  });

  it('returns yellow for >= 0.35', () => {
    expect(qHealthColor(0.45)).toBe('var(--color-neon-yellow)');
    expect(qHealthColor(0.35)).toBe('var(--color-neon-yellow)');
  });

  it('returns red for < 0.35', () => {
    expect(qHealthColor(0.2)).toBe('var(--color-neon-red)');
  });
});

describe('stateColor', () => {
  it('returns correct color for each lifecycle state', () => {
    expect(stateColor('candidate')).toBe('#7a7a9e');
    expect(stateColor('active')).toBe('#4d8eff');
    expect(stateColor('mature')).toBe('#a855f7');
    expect(stateColor('archived')).toBe('#3a3a52');
  });

  it('returns fallback gray for unknown state', () => {
    expect(stateColor('nonexistent')).toBe('#7a7a9e');
  });

  it('returns fallback gray for empty string', () => {
    expect(stateColor('')).toBe('#7a7a9e');
  });
});

describe('highlight color constants', () => {
  it('exports explicit HIGHLIGHT_COLOR_HEX', () => {
    expect(HIGHLIGHT_COLOR_HEX).toBe('#00e5ff');
  });

  it('exports explicit SIMILARITY_EDGE_COLOR_HEX', () => {
    expect(SIMILARITY_EDGE_COLOR_HEX).toBe('#00e5ff');
  });
});

describe('stateColor — template state removed', () => {
  it('no longer recognizes template state (falls back to default gray)', () => {
    // After the refactor, 'template' is unknown and gets the fallback.
    expect(stateColor('template')).toBe('#7a7a9e');
  });
});
