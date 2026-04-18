import { describe, it, expect, vi, beforeEach } from 'vitest';
import { templatesStore, type Template } from './templates.svelte';

describe('templatesStore', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
    // Reset store state between tests
    templatesStore.templates = [];
    templatesStore.loading = false;
  });

  it('load() populates templates from pagination envelope', async () => {
    (fetch as any).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({
        total: 1, count: 1, offset: 0, has_more: false, next_offset: null,
        items: [{
          id: 't1', label: 'x', prompt: 'p', domain_label: 'general',
          score: 7.5, usage_count: 0, promoted_at: '2026-01-01',
          pattern_ids: [], source_cluster_id: null, source_optimization_id: null,
          project_id: null, strategy: null, retired_at: null,
          retired_reason: null, last_used_at: null,
        }],
      }),
    });
    await templatesStore.load(null);
    expect(templatesStore.templates.length).toBe(1);
    expect(templatesStore.templates[0].id).toBe('t1');
  });

  it('spawn() POSTs to /use and returns prompt', async () => {
    (fetch as any).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ id: 't1', prompt: 'copy-this-prompt', usage_count: 1 }),
    });
    const result = await templatesStore.spawn('t1');
    expect(result?.prompt).toBe('copy-this-prompt');
    expect(result?.usage_count).toBe(1);
  });

  it('retire() flips retired_at locally on 200', async () => {
    // Seed one template in store
    templatesStore.templates = [{ id: 't1', retired_at: null } as Template];
    (fetch as any).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ id: 't1', retired_at: '2026-01-02T00:00:00' }),
    });
    const ok = await templatesStore.retire('t1');
    expect(ok).toBe(true);
    expect(templatesStore.templates[0].retired_at).toBe('2026-01-02T00:00:00');
  });
});
