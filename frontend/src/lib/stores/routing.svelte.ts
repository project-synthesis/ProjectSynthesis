/**
 * Frontend tier resolver.
 *
 * Derives the effective execution tier from user preferences and system
 * capabilities. Mirrors the backend's `services/routing.py` `resolve_route()`
 * 5-tier priority chain so the UI can adapt before the user hits SYNTHESIZE.
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import { preferencesStore } from './preferences.svelte';
import { forgeStore } from './forge.svelte';
import { rateLimitStore } from './rate-limit.svelte';

export type EffectiveTier = 'internal' | 'sampling' | 'passthrough';

export type DegradationReason =
  | 'mcp_disconnected'
  | 'not_sampling_capable'
  | 'no_sampling_detected'
  | null;

const _PIPELINE_STATUSES = new Set(['analyzing', 'optimizing', 'scoring']);

/**
 * Effective execution tier — single flat derivation.
 *
 * During active pipeline execution (analyzing/optimizing/scoring), prefer
 * the backend's authoritative routing decision from the SSE stream. The
 * backend accounts for ``caller="rest"`` which blocks sampling tiers from
 * the web UI — the frontend prediction doesn't know about this constraint.
 *
 * When idle or complete, derive the tier from preferences + capabilities
 * (the 5-tier priority chain matching ``services/routing.py``).
 *
 * All reactive dependencies are read in a single callback to ensure
 * Svelte's fine-grained tracking catches every state change.
 */
let _tier = $derived.by((): EffectiveTier => {
  // --- Live overlay: backend's actual decision during pipeline execution ---
  const live = forgeStore.routingDecision;
  if (live && _PIPELINE_STATUSES.has(forgeStore.status)) {
    return live.tier as EffectiveTier;
  }

  // --- Predicted tier: 5-tier priority chain ---
  // 1. force_passthrough always wins (or rate limit active)
  if (preferencesStore.pipeline.force_passthrough || rateLimitStore.isAnyActive) return 'passthrough';

  // 2. force_sampling (if capable + connected)
  if (
    preferencesStore.pipeline.force_sampling &&
    forgeStore.samplingCapable === true &&
    !forgeStore.mcpDisconnected
  ) {
    return 'sampling';
  }

  // 3. Internal provider available
  if (forgeStore.provider) return 'internal';

  // 4. Auto-sampling
  if (forgeStore.samplingCapable === true && !forgeStore.mcpDisconnected) {
    return 'sampling';
  }

  // 5. Passthrough fallback
  return 'passthrough';
});

/** What the user explicitly asked for via force toggles, or null (auto). */
let _requestedTier = $derived.by((): EffectiveTier | null => {
  if (preferencesStore.pipeline.force_passthrough || rateLimitStore.isAnyActive) return 'passthrough';
  if (preferencesStore.pipeline.force_sampling) return 'sampling';
  return null;
});

/** True when force_sampling was requested but seamlessly using internal provider. */
let _isAutoFallback = $derived(
  _requestedTier === 'sampling' && _tier === 'internal',
);

/** True when a force override could not be honored AND no seamless fallback exists. */
let _isDegraded = $derived(
  _requestedTier !== null && _requestedTier !== _tier && !_isAutoFallback,
);

/** Why the requested tier could not be honored. */
let _degradationReason = $derived.by((): DegradationReason => {
  if (!_isDegraded) return null;
  if (_requestedTier === 'sampling') {
    if (forgeStore.mcpDisconnected) return 'mcp_disconnected';
    if (forgeStore.samplingCapable === false) return 'not_sampling_capable';
    if (forgeStore.samplingCapable === null) return 'no_sampling_detected';
  }
  return null;
});

/**
 * Build a degradation message for the passthrough fallback case.
 *
 * Only called when ``_isDegraded`` is true, which (with the ``_isAutoFallback``
 * guard) means the resolved tier is always ``passthrough``.
 */
function degradationMsg(reason: string): string {
  switch (reason) {
    case 'mcp_disconnected':
      return 'MCP disconnected \u2014 using passthrough';
    case 'not_sampling_capable':
      return 'MCP client does not support sampling \u2014 using passthrough';
    case 'no_sampling_detected':
      return 'No sampling-capable MCP client detected \u2014 using passthrough';
    default:
      return 'Tier unavailable \u2014 using passthrough';
  }
}

/** Unified read-only routing state for UI consumption. */
export const routing = {
  get tier(): EffectiveTier {
    return _tier;
  },
  get isPassthrough(): boolean {
    return _tier === 'passthrough';
  },
  get isSampling(): boolean {
    return _tier === 'sampling';
  },
  get isInternal(): boolean {
    return _tier === 'internal';
  },
  /** What the user explicitly asked for via force toggles, or null (auto). */
  get requestedTier(): EffectiveTier | null {
    return _requestedTier;
  },
  /** True when force_sampling requested but seamlessly using internal provider. */
  get isAutoFallback(): boolean {
    return _isAutoFallback;
  },
  /** Human-readable auto-fallback explanation, or null. */
  get autoFallbackMessage(): string | null {
    if (!_isAutoFallback) return null;
    const provider = forgeStore.provider?.toLowerCase().includes('cli') ? 'CLI' : 'Provider';
    return `${provider} active \u2014 sampling resumes on IDE activity`;
  },
  /** True when a force override could not be honored AND no seamless fallback. */
  get isDegraded(): boolean {
    return _isDegraded;
  },
  /** Why the requested tier could not be honored. */
  get degradationReason(): DegradationReason {
    return _degradationReason;
  },
  /** Human-readable degradation explanation, or null. */
  get degradationMessage(): string | null {
    if (!_degradationReason) return null;
    return degradationMsg(_degradationReason);
  },
  /** CSS variable for the tier accent color. */
  get tierColor(): string {
    if (_tier === 'sampling') return 'var(--color-neon-green)';
    if (_tier === 'passthrough') return 'var(--color-neon-yellow)';
    return 'var(--color-neon-cyan)';
  },
  /** Raw RGB triplet for rgba() usage (hover backgrounds, focus rings). */
  get tierColorRgb(): string {
    if (_tier === 'sampling') return '34, 255, 136';
    if (_tier === 'passthrough') return '251, 191, 36';
    return '0, 229, 255';
  },
};
