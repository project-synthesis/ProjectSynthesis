import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Mock API calls to keep this test focused on PromptEdit behaviour
vi.mock('$lib/api/client', () => ({
  getStrategies: vi.fn().mockResolvedValue([]),
  optimizeSSE: vi.fn(),
  getOptimization: vi.fn(),
  submitFeedback: vi.fn(),
  savePassthrough: vi.fn(),
  getHealth: vi.fn().mockResolvedValue({ provider: 'claude-cli', version: '0.1.0' }),
}));

import PromptEdit from './PromptEdit.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { patternsStore } from '$lib/stores/patterns.svelte';

describe('PromptEdit', () => {
  beforeEach(() => {
    forgeStore._reset();
    patternsStore._reset();
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
});
