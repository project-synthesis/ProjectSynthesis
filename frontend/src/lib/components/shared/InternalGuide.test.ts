import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));

import InternalGuide from './InternalGuide.svelte';
import { internalGuide } from '$lib/stores/internal-guide.svelte';

describe('InternalGuide', () => {
  beforeEach(() => {
    internalGuide.close();
  });
  afterEach(() => {
    cleanup();
    internalGuide.close();
  });

  it('does not render when closed', () => {
    render(InternalGuide);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders the header and "WHY INTERNAL" section when open', () => {
    internalGuide.show(false);
    render(InternalGuide);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('INTERNAL PROVIDER')).toBeInTheDocument();
    expect(screen.getByText('WHY INTERNAL')).toBeInTheDocument();
  });

  it('renders the CLI-specific copy when provider includes "cli"', () => {
    internalGuide.show(false);
    render(InternalGuide, { props: { provider: 'claude_cli' } });
    expect(screen.getByText(/Claude CLI detected/)).toBeInTheDocument();
    expect(screen.getByText(/zero marginal cost via Claude CLI/)).toBeInTheDocument();
  });

  it('renders the API-key copy when provider does not include "cli"', () => {
    internalGuide.show(false);
    render(InternalGuide, { props: { provider: 'anthropic_api' } });
    // Step 1's description carries the API-mode variant — Direct access with
    // prompt caching and streaming.
    expect(screen.getByText(/Anthropic API key configured/)).toBeInTheDocument();
    // Also exercise the "WHY INTERNAL" text which differs between modes.
    expect(screen.getByText(/Full pipeline via Anthropic API/)).toBeInTheDocument();
  });

  it('renders all five step titles', () => {
    internalGuide.show(false);
    render(InternalGuide);
    expect(screen.getByText('Provider detected')).toBeInTheDocument();
    expect(screen.getByText('Full 3-phase pipeline')).toBeInTheDocument();
    expect(screen.getByText('Real-time progress')).toBeInTheDocument();
    expect(screen.getByText('All features enabled')).toBeInTheDocument();
    expect(screen.getByText('Codebase context + patterns')).toBeInTheDocument();
  });
});
