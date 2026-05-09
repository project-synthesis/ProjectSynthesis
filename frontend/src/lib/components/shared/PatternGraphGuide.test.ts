import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));

import PatternGraphGuide from './PatternGraphGuide.svelte';
import { patternGraphGuide } from '$lib/stores/pattern-graph-guide.svelte';

describe('PatternGraphGuide', () => {
  beforeEach(() => {
    patternGraphGuide.close();
  });
  afterEach(() => {
    cleanup();
    patternGraphGuide.close();
  });

  it('does not render when closed', () => {
    render(PatternGraphGuide);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders header + WHY block when open', () => {
    patternGraphGuide.show(false);
    render(PatternGraphGuide);
    expect(screen.getByText('PATTERN GRAPH')).toBeInTheDocument();
    expect(screen.getByText('IMMERSIVE VISUALIZATION')).toBeInTheDocument();
    expect(screen.getByText(/The graph IS the interface/)).toBeInTheDocument();
  });

  it('renders all four step titles', () => {
    patternGraphGuide.show(false);
    render(PatternGraphGuide);
    expect(screen.getByText('Navigate the graph')).toBeInTheDocument();
    expect(screen.getByText('Inspect clusters')).toBeInTheDocument();
    expect(screen.getByText('Access controls')).toBeInTheDocument();
    expect(screen.getByText('View metrics & search')).toBeInTheDocument();
  });
});
