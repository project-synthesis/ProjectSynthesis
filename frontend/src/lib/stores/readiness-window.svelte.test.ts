import { describe, it, expect, beforeEach } from 'vitest';
import { readinessWindowStore } from './readiness-window.svelte';

const STORAGE_KEY = 'synthesis:readiness_window';

describe('readinessWindowStore', () => {
  beforeEach(() => {
    localStorage.clear();
    readinessWindowStore._reset();
  });

  it('defaults to 24h when no value is persisted', () => {
    expect(readinessWindowStore.window).toBe('24h');
  });

  it('set() updates the in-memory value and persists to localStorage', () => {
    readinessWindowStore.set('7d');
    expect(readinessWindowStore.window).toBe('7d');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('7d');
  });

  it('accepts all three valid windows (24h, 7d, 30d)', () => {
    readinessWindowStore.set('24h');
    expect(readinessWindowStore.window).toBe('24h');
    readinessWindowStore.set('7d');
    expect(readinessWindowStore.window).toBe('7d');
    readinessWindowStore.set('30d');
    expect(readinessWindowStore.window).toBe('30d');
  });

  it('_reset() clears the persisted value and returns to default', () => {
    readinessWindowStore.set('30d');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('30d');
    readinessWindowStore._reset();
    expect(readinessWindowStore.window).toBe('24h');
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('ignores invalid persisted values and falls back to 24h', () => {
    localStorage.setItem(STORAGE_KEY, 'bogus');
    readinessWindowStore._reloadForTest();
    expect(readinessWindowStore.window).toBe('24h');
  });

  it('restores a valid persisted value on load', () => {
    localStorage.setItem(STORAGE_KEY, '7d');
    readinessWindowStore._reloadForTest();
    expect(readinessWindowStore.window).toBe('7d');
  });
});
