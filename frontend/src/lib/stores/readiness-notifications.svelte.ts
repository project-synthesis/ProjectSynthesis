import { preferencesStore } from './preferences.svelte';
import { toastStore } from './toast.svelte';

export interface ReadinessCrossingPayload {
  domain_id: string;
  domain_label: string;
  axis: 'stability' | 'emergence';
  from_tier: string;
  to_tier: string;
  consistency: number;
  gap_to_threshold: number | null;
  would_dissolve: boolean;
  ts: string;
}

export function formatCrossingMessage(payload: ReadinessCrossingPayload): string {
  const label = String(payload.domain_label).toLowerCase();
  const axis = String(payload.axis).toLowerCase();
  const toTier = String(payload.to_tier).toLowerCase();
  const base = `${label} — ${axis} ${toTier}`;
  return payload.would_dissolve ? `${base} (will dissolve)` : base;
}

export function dispatchReadinessCrossing(payload: ReadinessCrossingPayload): void {
  if (!payload || !payload.domain_id || !payload.domain_label || !payload.axis || !payload.to_tier) {
    return;
  }
  const prefs = preferencesStore.prefs.domain_readiness_notifications;
  if (!prefs || prefs.enabled !== true) return;
  if (Array.isArray(prefs.muted_domain_ids) && prefs.muted_domain_ids.includes(payload.domain_id)) {
    return;
  }
  toastStore.info(formatCrossingMessage(payload));
}
