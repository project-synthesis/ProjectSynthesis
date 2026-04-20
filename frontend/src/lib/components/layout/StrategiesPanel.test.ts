import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { mockFetch } from '$lib/test-utils';
import StrategiesPanel from './StrategiesPanel.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';

const strategies = [
  { name: 'chain-of-thought', tagline: 'Step-by-step reasoning', description: 'desc' },
  { name: 'few-shot', tagline: 'Examples-driven', description: 'desc' },
];

describe('StrategiesPanel — smoke', () => {
  beforeEach(() => {
    forgeStore.strategy = null;
    mockFetch([]);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mounts and renders the strategies list', () => {
    render(StrategiesPanel, { props: { strategies, onSaved: vi.fn() } });
    expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    expect(screen.getByText('few-shot')).toBeInTheDocument();
  });

  it('renders empty-state note when strategies list is empty', () => {
    render(StrategiesPanel, { props: { strategies: [], onSaved: vi.fn() } });
    expect(screen.getByText(/No strategy files found/i)).toBeInTheDocument();
  });

  it('clicking a strategy sets forgeStore.strategy', async () => {
    const user = userEvent.setup();
    render(StrategiesPanel, { props: { strategies, onSaved: vi.fn() } });

    await user.click(screen.getByText('chain-of-thought'));
    expect(forgeStore.strategy).toBe('chain-of-thought');
  });

  it('clicking an already-active strategy toggles it off', async () => {
    const user = userEvent.setup();
    forgeStore.strategy = 'few-shot';
    render(StrategiesPanel, { props: { strategies, onSaved: vi.fn() } });

    await user.click(screen.getByText('few-shot'));
    expect(forgeStore.strategy).toBeNull();
  });
});
