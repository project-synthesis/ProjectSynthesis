import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import TemplatesSection from './TemplatesSection.svelte';
import { templatesStore, type Template } from '$lib/stores/templates.svelte';

function mkTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 't-1',
    source_cluster_id: 'c-1',
    source_optimization_id: 'o-1',
    project_id: null,
    label: 'Mock template',
    prompt: 'Mock prompt',
    strategy: 'chain-of-thought',
    score: 8.2,
    pattern_ids: [],
    domain_label: 'backend',
    promoted_at: '2026-04-15T00:00:00Z',
    retired_at: null,
    retired_reason: null,
    usage_count: 0,
    last_used_at: null,
    ...overrides,
  };
}

describe('TemplatesSection', () => {
  beforeEach(() => {
    templatesStore.templates = [];
    templatesStore.loading = false;
  });

  afterEach(() => {
    cleanup();
    templatesStore.templates = [];
    vi.restoreAllMocks();
  });

  it('renders nothing when templates store is empty', () => {
    render(TemplatesSection);
    expect(screen.queryByText('PROVEN TEMPLATES')).not.toBeInTheDocument();
  });

  it('renders section header + templates when store is populated', () => {
    templatesStore.templates = [
      mkTemplate({ id: 't-1', label: 'Auth flow', domain_label: 'backend' }),
    ];
    render(TemplatesSection);
    expect(screen.getByText('PROVEN TEMPLATES')).toBeInTheDocument();
    expect(screen.getByText('Auth flow')).toBeInTheDocument();
  });

  it('filters out retired templates', () => {
    templatesStore.templates = [
      mkTemplate({ id: 't-1', label: 'Alive', retired_at: null }),
      mkTemplate({ id: 't-2', label: 'Gone', retired_at: '2026-04-18T00:00:00Z' }),
    ];
    render(TemplatesSection);
    expect(screen.getByText('Alive')).toBeInTheDocument();
    expect(screen.queryByText('Gone')).not.toBeInTheDocument();
  });

  it('groups templates by frozen domain_label', () => {
    templatesStore.templates = [
      mkTemplate({ id: 't-1', label: 'A', domain_label: 'backend' }),
      mkTemplate({ id: 't-2', label: 'B', domain_label: 'data' }),
    ];
    const { container } = render(TemplatesSection);
    expect(container.querySelector('[data-group-header="backend"]')).not.toBeNull();
    expect(container.querySelector('[data-group-header="data"]')).not.toBeNull();
  });

  it('renders Use and retire buttons per template', () => {
    templatesStore.templates = [
      mkTemplate({ id: 't-1', label: 'Test template' }),
    ];
    render(TemplatesSection);
    expect(screen.getByRole('button', { name: /use template test template/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retire template test template/i })).toBeInTheDocument();
  });

  it('shows pattern count and usage badges when > 0', () => {
    templatesStore.templates = [
      mkTemplate({ id: 't-1', label: 'With patterns', pattern_ids: ['p1', 'p2'], usage_count: 5 }),
    ];
    render(TemplatesSection);
    expect(screen.getByText('2 patterns')).toBeInTheDocument();
    expect(screen.getByText('5× used')).toBeInTheDocument();
  });
});
