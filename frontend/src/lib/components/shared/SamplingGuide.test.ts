import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));

import SamplingGuide from './SamplingGuide.svelte';
import { samplingGuide } from '$lib/stores/sampling-guide.svelte';

describe('SamplingGuide', () => {
  beforeEach(() => {
    samplingGuide.close();
  });
  afterEach(() => {
    cleanup();
    samplingGuide.close();
  });

  it('does not render when closed', () => {
    render(SamplingGuide);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders header + WHY section when opened', () => {
    samplingGuide.show(false);
    render(SamplingGuide);
    expect(screen.getByText('MCP SAMPLING PIPELINE')).toBeInTheDocument();
    expect(screen.getByText('WHY SAMPLING')).toBeInTheDocument();
    expect(screen.getByText(/MCP Copilot Bridge/)).toBeInTheDocument();
  });

  it('renders all five step titles', () => {
    samplingGuide.show(false);
    render(SamplingGuide);
    expect(screen.getByText('IDE connects')).toBeInTheDocument();
    expect(screen.getByText('Pipeline runs through your IDE')).toBeInTheDocument();
    expect(screen.getByText('Full context injected')).toBeInTheDocument();
    expect(screen.getByText('Hybrid scoring')).toBeInTheDocument();
    expect(screen.getByText('Auto-fallback')).toBeInTheDocument();
  });
});
