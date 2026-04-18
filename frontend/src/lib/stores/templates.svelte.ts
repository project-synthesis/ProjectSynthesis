/**
 * Templates store — reactive, API-driven list of promoted prompt templates.
 *
 * Backend contract: `backend/app/routers/templates.py`.
 * Three operations:
 *   - load(projectId) — fetch all templates, optionally scoped to a project
 *   - spawn(id)       — POST /use to record usage and retrieve prompt for copying
 *   - retire(id)      — POST /retire to soft-delete a template
 */

import { tryFetch } from '$lib/api/client';

export interface Template {
  id: string;
  source_cluster_id: string | null;
  source_optimization_id: string | null;
  project_id: string | null;
  label: string;
  prompt: string;
  strategy: string | null;
  score: number;
  pattern_ids: string[];
  domain_label: string;
  promoted_at: string;
  retired_at: string | null;
  retired_reason: string | null;
  usage_count: number;
  last_used_at: string | null;
}

export interface SpawnResult {
  id: string;
  prompt: string;
  usage_count: number;
}

interface TemplateListResponse {
  total: number;
  count: number;
  offset: number;
  has_more: boolean;
  next_offset: number | null;
  items: Template[];
}

interface UseResponse {
  id: string;
  prompt: string;
  usage_count: number;
}

interface RetireResponse {
  id: string;
  retired_at: string;
}

class TemplatesStore {
  templates = $state<Template[]>([]);
  loading = $state(false);

  async load(projectId: string | null): Promise<void> {
    this.loading = true;
    try {
      const path = projectId
        ? `/templates?project_id=${encodeURIComponent(projectId)}`
        : '/templates';
      const body = await tryFetch<TemplateListResponse>(path);
      if (!body) return;
      this.templates = body.items;
    } finally {
      this.loading = false;
    }
  }

  async spawn(templateId: string): Promise<SpawnResult | null> {
    const body = await tryFetch<UseResponse>(`/templates/${templateId}/use`, { method: 'POST' });
    if (!body) return null;
    const idx = this.templates.findIndex((t) => t.id === templateId);
    if (idx !== -1) {
      this.templates[idx] = { ...this.templates[idx], usage_count: body.usage_count };
    }
    return { id: body.id, prompt: body.prompt, usage_count: body.usage_count };
  }

  async retire(templateId: string): Promise<boolean> {
    const body = await tryFetch<RetireResponse>(`/templates/${templateId}/retire`, { method: 'POST' });
    if (!body) return false;
    const idx = this.templates.findIndex((t) => t.id === templateId);
    if (idx !== -1) {
      this.templates[idx] = { ...this.templates[idx], retired_at: body.retired_at };
    }
    return true;
  }
}

export const templatesStore = new TemplatesStore();
