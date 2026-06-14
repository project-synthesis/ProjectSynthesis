import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { clustersStore } from './clusters.svelte';
import * as clustersApi from '$lib/api/clusters';

describe('clustersStore — preview enrichment (Tier 2)', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('exposes a preview state initially null', () => {
    expect(clustersStore.preview).toBeNull();
    expect(clustersStore._previewInFlight).toBe(false);
    expect(clustersStore._previewError).toBeNull();
    expect(clustersStore._lastPreviewedText).toBeNull();
  });

  it('typing-debounce fires previewEnrichment on the 800ms tick', async () => {
    const fake: clustersApi.EnrichmentPreview = {
      task_type: { task_type: 'coding', confidence: 0.8, signal_source: 'bootstrap' },
      domain: 'backend',
      intent_label: 'build a function',
      recommended_strategy: 'meta-prompting',
      top_strategies: [],
      blocked_strategies: [],
      weaknesses: [],
      divergence_alerts: [],
      domain_relaxed_fallback: false,
      elapsed_ms: 42,
    };
    const spy = vi.spyOn(clustersApi, 'previewEnrichment').mockResolvedValue(fake);
    vi.spyOn(clustersApi, 'matchPattern').mockResolvedValue({ match: null });

    const longText = 'a'.repeat(40);
    clustersStore.checkForPatterns(longText);

    // Before the 800ms tick elapses, no calls fired yet.
    expect(spy).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(900);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(clustersStore.preview).toEqual(fake);
    expect(clustersStore._lastPreviewedText).toBe(longText);
  });

  it('AbortController cancels in-flight preview when prompt changes again', async () => {
    const aborted: string[] = [];
    vi.spyOn(clustersApi, 'previewEnrichment').mockImplementation(
      async (_text, _pid, signal) => {
        signal.addEventListener('abort', () => aborted.push('aborted'));
        await new Promise((resolve) => setTimeout(resolve, 5000));
        return null;
      },
    );
    vi.spyOn(clustersApi, 'matchPattern').mockResolvedValue({ match: null });

    clustersStore.checkForPatterns('a'.repeat(40));
    await vi.advanceTimersByTimeAsync(900);
    clustersStore.checkForPatterns('b'.repeat(40));
    await vi.advanceTimersByTimeAsync(900);

    expect(aborted.length).toBeGreaterThanOrEqual(1);
  });

  it('guards same-text re-fires via _lastPreviewedText', async () => {
    const spy = vi.spyOn(clustersApi, 'previewEnrichment').mockResolvedValue({
      task_type: { task_type: 'coding', confidence: 0.8, signal_source: 'bootstrap' },
      domain: 'backend',
      intent_label: '',
      recommended_strategy: 'auto',
      top_strategies: [],
      blocked_strategies: [],
      weaknesses: [],
      divergence_alerts: [],
      domain_relaxed_fallback: false,
      elapsed_ms: 1,
    });
    vi.spyOn(clustersApi, 'matchPattern').mockResolvedValue({ match: null });

    const text = 'x'.repeat(40);
    clustersStore.checkForPatterns(text);
    await vi.advanceTimersByTimeAsync(900);
    clustersStore.checkForPatterns(text);
    await vi.advanceTimersByTimeAsync(900);

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it('resets preview to null when prompt drops below MIN_PROMPT_LENGTH', async () => {
    vi.spyOn(clustersApi, 'previewEnrichment').mockResolvedValue({
      task_type: { task_type: 'coding', confidence: 0.8, signal_source: 'bootstrap' },
      domain: 'backend',
      intent_label: '',
      recommended_strategy: 'auto',
      top_strategies: [],
      blocked_strategies: [],
      weaknesses: [],
      divergence_alerts: [],
      domain_relaxed_fallback: false,
      elapsed_ms: 1,
    });
    vi.spyOn(clustersApi, 'matchPattern').mockResolvedValue({ match: null });

    clustersStore.checkForPatterns('a'.repeat(40));
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore.preview).not.toBeNull();

    clustersStore.checkForPatterns('short');  // below MIN_PROMPT_LENGTH=30
    expect(clustersStore.preview).toBeNull();
    expect(clustersStore._lastPreviewedText).toBeNull();
  });
});
