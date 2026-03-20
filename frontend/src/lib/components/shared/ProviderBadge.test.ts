import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ProviderBadge from './ProviderBadge.svelte';

describe('ProviderBadge', () => {
  afterEach(() => {
    cleanup();
  });

  it('displays CLI label for cli provider string', () => {
    render(ProviderBadge, { props: { provider: 'claude-cli' } });
    expect(screen.getByText('CLI')).toBeInTheDocument();
  });

  it('displays API label for anthropic api provider string', () => {
    render(ProviderBadge, { props: { provider: 'anthropic-api' } });
    expect(screen.getByText('API')).toBeInTheDocument();
  });

  it('displays API label for provider string containing "api"', () => {
    render(ProviderBadge, { props: { provider: 'api-provider' } });
    expect(screen.getByText('API')).toBeInTheDocument();
  });

  it('displays MCP label for mcp provider string', () => {
    render(ProviderBadge, { props: { provider: 'mcp-server' } });
    expect(screen.getByText('MCP')).toBeInTheDocument();
  });

  it('displays PASSTHROUGH label when provider is null', () => {
    render(ProviderBadge, { props: { provider: null } });
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  it('displays PASSTHROUGH label when provider is not provided', () => {
    render(ProviderBadge);
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  it('has accessible aria-label for CLI variant', () => {
    render(ProviderBadge, { props: { provider: 'claude-cli' } });
    const badge = screen.getByLabelText('Active provider: CLI');
    expect(badge).toBeInTheDocument();
  });

  it('has accessible aria-label for PASSTHROUGH variant', () => {
    render(ProviderBadge, { props: { provider: null } });
    const badge = screen.getByLabelText('Active provider: PASSTHROUGH');
    expect(badge).toBeInTheDocument();
  });

  it('shows title attribute with provider value', () => {
    render(ProviderBadge, { props: { provider: 'claude-cli' } });
    const badge = screen.getByLabelText('Active provider: CLI');
    expect(badge).toHaveAttribute('title', 'Provider: claude-cli');
  });

  it('shows title "Provider: None" when provider is null', () => {
    render(ProviderBadge, { props: { provider: null } });
    const badge = screen.getByLabelText('Active provider: PASSTHROUGH');
    expect(badge).toHaveAttribute('title', 'Provider: None');
  });

  it('truncates unknown provider to 4 uppercase chars', () => {
    render(ProviderBadge, { props: { provider: 'vertex' } });
    // 'vertex' → no cli/mcp/api → 'VERT'
    expect(screen.getByText('VERT')).toBeInTheDocument();
  });
});
