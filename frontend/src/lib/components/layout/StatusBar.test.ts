import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { mockFetch, mockHealthResponse } from '$lib/test-utils';

// Mock the patterns API to avoid errors
vi.mock('$lib/api/patterns', () => ({
  getPatternStats: vi.fn().mockResolvedValue({ total_families: 0 }),
  matchPattern: vi.fn(),
  getPatternGraph: vi.fn(),
  getFamilyDetail: vi.fn(),
}));

import StatusBar from './StatusBar.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { patternsStore } from '$lib/stores/patterns.svelte';

describe('StatusBar', () => {
  beforeEach(() => {
    forgeStore._reset();
    patternsStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the status bar element', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    expect(screen.getByRole('status', { name: 'Status bar' })).toBeInTheDocument();
  });

  it('shows Ctrl+K keyboard shortcut hint', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    expect(screen.getByLabelText('Open command palette with Ctrl+K')).toBeInTheDocument();
    expect(screen.getByText('Ctrl+K')).toBeInTheDocument();
  });

  it('shows provider badge (PASSTHROUGH when no provider loaded yet)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    // ProviderBadge renders PASSTHROUGH when provider is null (initial state)
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  it('shows pattern count when patternCount > 0', async () => {
    const { getPatternStats } = await import('$lib/api/patterns');
    vi.mocked(getPatternStats).mockResolvedValue({ total_families: 7 } as never);
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    // Wait for async effect
    await vi.waitFor(() => {
      expect(screen.queryByText('7 patterns')).toBeInTheDocument();
    });
  });

  it('does not show pattern count when patternCount is 0', async () => {
    const { getPatternStats } = await import('$lib/api/patterns');
    vi.mocked(getPatternStats).mockResolvedValue({ total_families: 0 } as never);
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/patterns/)).not.toBeInTheDocument();
  });

  it('shows "disconnected" label when MCP is disconnected', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.mcpDisconnected = true;
    render(StatusBar);
    expect(screen.getByText('disconnected')).toBeInTheDocument();
  });

  it('does not show "disconnected" when MCP is connected', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.mcpDisconnected = false;
    render(StatusBar);
    expect(screen.queryByText('disconnected')).not.toBeInTheDocument();
  });

  it('shows phase label while forge is analyzing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'analyzing';
    render(StatusBar);
    expect(screen.getByText(/analyzing\.\.\./i)).toBeInTheDocument();
  });

  it('shows phase label while forge is optimizing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'optimizing';
    render(StatusBar);
    expect(screen.getByText(/optimizing\.\.\./i)).toBeInTheDocument();
  });

  it('shows version from health API after load', async () => {
    mockFetch([{
      match: '/api/health',
      response: mockHealthResponse({ version: '1.2.3' }),
    }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.queryByText('v1.2.3')).toBeInTheDocument();
    });
  });
});
