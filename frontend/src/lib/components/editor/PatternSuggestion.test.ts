import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import PatternSuggestion from './PatternSuggestion.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { mockClusterMatch, mockMetaPattern } from '$lib/test-utils';

describe('PatternSuggestion', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  function makeSuggestion(overrides: Record<string, unknown> = {}) {
    return mockClusterMatch({
      cluster: { id: 'fam-1', label: 'API endpoint patterns', domain: 'backend', member_count: 3 },
      meta_patterns: [
        mockMetaPattern({ id: 'mp-1', pattern_text: 'Include error handling', source_count: 5 }),
        mockMetaPattern({ id: 'mp-2', pattern_text: 'Use consistent naming', source_count: 3 }),
      ],
      similarity: 0.85,
      ...overrides,
    });
  }

  it('renders nothing when suggestion is not visible', () => {
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('renders nothing when suggestion is null even if visible flag is set', () => {
    clustersStore.suggestion = null;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows the suggestion banner when suggestion is visible', () => {
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('displays the cluster label in the suggestion banner', () => {
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.getByText('API endpoint patterns')).toBeInTheDocument();
  });

  it('displays similarity percentage', () => {
    clustersStore.suggestion = makeSuggestion({ similarity: 0.85 }) as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.getByText(/85%/)).toBeInTheDocument();
  });

  it('shows pattern text previews', () => {
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.getByText('Include error handling')).toBeInTheDocument();
    expect(screen.getByText('Use consistent naming')).toBeInTheDocument();
  });

  it('renders Apply with count and Skip buttons', () => {
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });
    expect(screen.getByRole('button', { name: /Apply 2/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skip' })).toBeInTheDocument();
  });

  it('calls applySuggestion and onApply with pattern IDs and label when Apply is clicked', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply } });

    await user.click(screen.getByRole('button', { name: /Apply/ }));
    expect(onApply).toHaveBeenCalledWith({ ids: ['mp-1', 'mp-2'], clusterLabel: 'API endpoint patterns' });
  });

  it('calls dismissSuggestion when Skip is clicked', async () => {
    const user = userEvent.setup();
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    const dismissSpy = vi.spyOn(clustersStore, 'dismissSuggestion');
    render(PatternSuggestion, { props: { onApply: vi.fn() } });

    await user.click(screen.getByRole('button', { name: 'Skip' }));
    expect(dismissSpy).toHaveBeenCalled();
  });

  it('dismisses suggestion after Skip (banner no longer visible)', async () => {
    const user = userEvent.setup();
    clustersStore.suggestion = makeSuggestion() as never;
    clustersStore.suggestionVisible = true;
    render(PatternSuggestion, { props: { onApply: vi.fn() } });

    expect(screen.getByRole('alert')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Skip' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
