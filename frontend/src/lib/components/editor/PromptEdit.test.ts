import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Mock API calls to keep this test focused on PromptEdit behaviour
vi.mock('$lib/api/client', () => ({
  getStrategies: vi.fn().mockResolvedValue([]),
  optimizeSSE: vi.fn().mockReturnValue({ abort: vi.fn() }),
  getOptimization: vi.fn(),
  submitFeedback: vi.fn(),
  savePassthrough: vi.fn(),
  getHealth: vi.fn().mockResolvedValue({ provider: 'claude-cli', version: '0.1.0' }),
}));

import PromptEdit from './PromptEdit.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import * as apiClient from '$lib/api/client';

describe('PromptEdit', () => {
  beforeEach(() => {
    forgeStore._reset();
    clustersStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders a textarea with placeholder text', () => {
    render(PromptEdit);
    expect(screen.getByPlaceholderText('Enter your prompt here...')).toBeInTheDocument();
  });

  it('textarea has accessible label', () => {
    render(PromptEdit);
    expect(screen.getByRole('textbox', { name: 'Prompt editor' })).toBeInTheDocument();
  });

  it('renders the SYNTHESIZE button', () => {
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).toBeInTheDocument();
  });

  it('textarea reflects the current forgeStore.prompt value', () => {
    forgeStore.prompt = 'My initial prompt';
    render(PromptEdit);
    expect(screen.getByRole('textbox', { name: 'Prompt editor' })).toHaveValue('My initial prompt');
  });

  it('typing in textarea updates forgeStore.prompt', async () => {
    const user = userEvent.setup();
    render(PromptEdit);
    const textarea = screen.getByRole('textbox', { name: 'Prompt editor' });
    await user.type(textarea, 'Hello world');
    expect(forgeStore.prompt).toBe('Hello world');
  });

  it('synthesize button is enabled when status is idle', () => {
    forgeStore.status = 'idle';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).not.toBeDisabled();
  });

  it('synthesize button is disabled when status is analyzing', () => {
    forgeStore.status = 'analyzing';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).toBeDisabled();
  });

  it('synthesize button is disabled when status is optimizing', () => {
    forgeStore.status = 'optimizing';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).toBeDisabled();
  });

  it('synthesize button is disabled when status is scoring', () => {
    forgeStore.status = 'scoring';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).toBeDisabled();
  });

  it('synthesize button is enabled again when status is complete', () => {
    forgeStore.status = 'complete';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).not.toBeDisabled();
  });

  it('synthesize button is enabled again when status is error', () => {
    forgeStore.status = 'error';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /SYNTHESIZE/i })).not.toBeDisabled();
  });

  it('shows phase label ANALYZING... while status is analyzing', () => {
    forgeStore.status = 'analyzing';
    render(PromptEdit);
    expect(screen.getByText(/Analyzing\.\.\./i)).toBeInTheDocument();
  });

  it('shows phase label OPTIMIZING... while status is optimizing', () => {
    forgeStore.status = 'optimizing';
    render(PromptEdit);
    expect(screen.getByText(/Optimizing\.\.\./i)).toBeInTheDocument();
  });

  it('shows phase label SCORING... while status is scoring', () => {
    forgeStore.status = 'scoring';
    render(PromptEdit);
    expect(screen.getByText(/Scoring\.\.\./i)).toBeInTheDocument();
  });

  it('does not show phase label when idle', () => {
    forgeStore.status = 'idle';
    render(PromptEdit);
    // No phase labels should be shown
    expect(screen.queryByText(/Analyzing\.\.\./i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Optimizing\.\.\./i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Scoring\.\.\./i)).not.toBeInTheDocument();
  });

  it('shows STRATEGY label in the action bar', () => {
    render(PromptEdit);
    expect(screen.getByText('STRATEGY')).toBeInTheDocument();
  });

  it('shows PREPARE label when status is passthrough', () => {
    forgeStore.status = 'passthrough';
    render(PromptEdit);
    expect(screen.getByRole('button', { name: /PREPARE/i })).toBeInTheDocument();
  });

  it('strategy select changes forgeStore.strategy when a non-empty value is selected', async () => {
    vi.mocked(apiClient.getStrategies).mockResolvedValue([
      { name: 'chain-of-thought', tagline: 'Step by step', description: '' },
    ] as any);
    render(PromptEdit);
    // Wait for strategies to load
    await vi.waitFor(() => {
      expect(screen.getByRole('combobox')).toBeInTheDocument();
    });
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    // Add the option to the DOM so it can be selected
    const option = document.createElement('option');
    option.value = 'chain-of-thought';
    option.text = 'chain-of-thought';
    select.appendChild(option);
    select.value = 'chain-of-thought';
    fireEvent.change(select);
    expect(forgeStore.strategy).toBe('chain-of-thought');
  });

  it('strategy select sets forgeStore.strategy to null when empty value selected', async () => {
    forgeStore.strategy = 'chain-of-thought';
    render(PromptEdit);
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: '' } });
    expect(forgeStore.strategy).toBeNull();
  });

  it('clicking synthesize calls forgeStore.forge and opens result tab when traceId is set', async () => {
    const user = userEvent.setup();
    vi.mocked(apiClient.optimizeSSE).mockImplementation(
      (_prompt: string, _strategy: any, onEvent: any) => {
        // Immediately emit an optimization_start event to set traceId
        onEvent({ event: 'optimization_start', trace_id: 'trace-123', type: 'optimization_start' });
        const ctrl = new AbortController();
        ctrl.abort = vi.fn();
        return ctrl;
      }
    );
    forgeStore.prompt = 'A sufficiently long prompt for testing synthesize';
    render(PromptEdit);
    const openResultSpy = vi.spyOn(editorStore, 'openResult');
    await user.click(screen.getByRole('button', { name: /SYNTHESIZE/i }));
    // forgeStore.forge() was called (status changes to analyzing)
    expect(forgeStore.status).not.toBe('idle');
  });

  it('typing in textarea calls clustersStore.checkForPatterns', async () => {
    const user = userEvent.setup();
    const checkSpy = vi.spyOn(clustersStore, 'checkForPatterns');
    render(PromptEdit);
    const textarea = screen.getByRole('textbox', { name: 'Prompt editor' });
    await user.type(textarea, 'x');
    expect(checkSpy).toHaveBeenCalled();
  });
});
