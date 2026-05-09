import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import RateLimitBanner from './RateLimitBanner.svelte';
import { rateLimitStore } from '$lib/stores/rate-limit.svelte';

describe('RateLimitBanner', () => {
  beforeEach(() => {
    rateLimitStore._reset();
  });
  afterEach(() => {
    cleanup();
    rateLimitStore._reset();
  });

  it('renders nothing when no provider is rate-limited', () => {
    render(RateLimitBanner);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('renders the banner when a provider goes active', async () => {
    rateLimitStore.applyActive({
      provider: 'claude_cli',
      reset_at_iso: new Date(Date.now() + 60_000).toISOString(),
      estimated_wait_seconds: 60,
    });
    render(RateLimitBanner);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/Rate-limited/)).toBeInTheDocument();
    expect(screen.getByText(/Claude CLI/)).toBeInTheDocument();
    expect(screen.getByText(/passthrough/)).toBeInTheDocument();
  });

  it('falls back to raw provider name for unknown providers', () => {
    rateLimitStore.applyActive({
      provider: 'mystery_provider',
      reset_at_iso: new Date(Date.now() + 60_000).toISOString(),
    });
    render(RateLimitBanner);
    expect(screen.getByText(/mystery_provider/)).toBeInTheDocument();
  });

  it('renders "Anthropic API" label for the anthropic_api provider', () => {
    rateLimitStore.applyActive({
      provider: 'anthropic_api',
      reset_at_iso: new Date(Date.now() + 60_000).toISOString(),
    });
    render(RateLimitBanner);
    expect(screen.getByText(/Anthropic API/)).toBeInTheDocument();
  });

  it('renders "retry shortly" when seconds_remaining is null', () => {
    rateLimitStore.applyActive({
      provider: 'claude_cli',
      reset_at_iso: null,
      estimated_wait_seconds: null,
    });
    render(RateLimitBanner);
    expect(screen.getByText(/retry shortly/)).toBeInTheDocument();
  });

  it('clicking dismiss button hides the banner', async () => {
    const user = userEvent.setup();
    rateLimitStore.applyActive({
      provider: 'claude_cli',
      reset_at_iso: new Date(Date.now() + 60_000).toISOString(),
    });
    render(RateLimitBanner);
    const btn = screen.getByLabelText(/Dismiss rate-limit banner/);
    await user.click(btn);
    expect(screen.queryByRole('status')).toBeNull();
  });
});
