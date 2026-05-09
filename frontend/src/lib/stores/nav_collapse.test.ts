import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { navCollapse } from './nav_collapse.svelte';

const STORAGE_KEY = 'synthesis:navigator_collapsed';

describe('navCollapse store', () => {
  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY);
    navCollapse._reset();
  });
  afterEach(() => {
    localStorage.removeItem(STORAGE_KEY);
    navCollapse._reset();
  });

  it('default-open policy: every key reads as open and not collapsed', () => {
    expect(navCollapse.isCollapsed('readiness')).toBe(false);
    expect(navCollapse.isOpen('readiness')).toBe(true);
    expect(navCollapse.isCollapsed('domain:backend')).toBe(false);
  });

  it('toggle flips state and persists JSON array to localStorage', () => {
    navCollapse.toggle('readiness');
    expect(navCollapse.isCollapsed('readiness')).toBe(true);
    expect(navCollapse.isOpen('readiness')).toBe(false);

    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
    expect(stored).toContain('readiness');

    navCollapse.toggle('readiness');
    expect(navCollapse.isCollapsed('readiness')).toBe(false);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')).not.toContain('readiness');
  });

  it('set is idempotent — same value is a no-op (does not write)', () => {
    navCollapse.set('templates', false); // already false
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    navCollapse.set('templates', true);
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toContain('templates');
    navCollapse.set('templates', true); // already true — no rewrite
    // Re-read still equal
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toContain('templates');
  });

  it('multiple keys persist together as an array', () => {
    navCollapse.toggle('a');
    navCollapse.toggle('b');
    navCollapse.toggle('domain:frontend');
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toEqual(expect.arrayContaining(['a', 'b', 'domain:frontend']));
    expect(stored).toHaveLength(3);
  });

  it('_reset clears state and removes the key from localStorage', () => {
    navCollapse.toggle('readiness');
    expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull();
    navCollapse._reset();
    expect(navCollapse.isCollapsed('readiness')).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
