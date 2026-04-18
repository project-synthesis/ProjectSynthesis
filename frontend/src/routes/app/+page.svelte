<script lang="ts">
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { domainStore } from '$lib/stores/domains.svelte';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import type { Preferences } from '$lib/stores/preferences.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getHealth } from '$lib/api/client';
  import type { HealthResponse } from '$lib/api/client';
  import { triggerTierGuide } from '$lib/stores/tier-onboarding.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { updateStore } from '$lib/stores/update.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import { sseHealthStore } from '$lib/stores/sse-health.svelte';
  import {
    dispatchReadinessCrossing,
    type ReadinessCrossingPayload,
  } from '$lib/stores/readiness-notifications.svelte';

  let backendError = $state<string | null>(null);
  let firstHealthReceived = false;
  let pendingGuide = false;
  let pendingHealthDelta: { health: HealthResponse; delta: any } | null = null;

  /**
   * Reconcile force toggles with actual system capabilities.
   * Called after BOTH health and preferences are loaded to avoid races.
   */
  function reconcileToggles(h: HealthResponse, delta: any): void {
    // Sampling just became available → auto-enable force_sampling, clear force_passthrough
    if (delta.samplingChanged) {
      onSamplingDetected();
      if (!preferencesStore.pipeline.force_sampling) {
        preferencesStore.setPipelineToggle('force_sampling', true);
      }
    }
    // Sampling no longer available → clear stale force_sampling instantly
    // (optimistic local update BEFORE async API call to prevent UI flash)
    if (preferencesStore.pipeline.force_sampling && h.sampling_capable !== true) {
      const prev = preferencesStore.prefs.pipeline.force_sampling;
      preferencesStore.prefs.pipeline.force_sampling = false;
      preferencesStore.setPipelineToggle('force_sampling', false).catch(() => {
        // Rollback optimistic update on API failure
        preferencesStore.prefs.pipeline.force_sampling = prev;
        addToast('deleted', 'Failed to update sampling preference');
      });
    }
  }

  /** Shared handler for new MCP sampling capability detection (DRY: SSE + health). */
  function onSamplingDetected(): void {
    addToast('created', 'MCP client connected with sampling capability');
    if (preferencesStore.pipeline.force_passthrough) {
      preferencesStore.setPipelineToggle('force_passthrough', false);
    }
  }

  // Real-time event stream — SSE health store owns the EventSource lifecycle
  // (latency tracking, degradation detection, exponential backoff reconnection).
  $effect(() => {
    sseHealthStore.connect(
      (type, data) => {
        // Sync events are handled by the store internally — skip here.
        if (type === 'sync') return;

        if (type === 'optimization_created' || type === 'optimization_analyzed' || type === 'refinement_turn') {
          window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
          // Toast for optimizations not from the current UI session (e.g., MCP)
          const isOwnTrace = data.trace_id === forgeStore.traceId || data.trace_id === forgeStore.passthroughTraceId;
          if (type !== 'refinement_turn' && data.trace_id && !isOwnTrace) {
            const label = type === 'optimization_analyzed' ? 'analyzed' : 'optimized';
            addToast('created', `Prompt ${label}`);
          }
          // Auto-load optimization results via event bus. Covers IDE-triggered
          // optimizations via MCP bridge — the web UI was idle but should show
          // the result (forgeStore.status = idle).
          if (type === 'optimization_created' && data.trace_id && data.status === 'completed') {
            const alreadyLoaded = forgeStore.result?.trace_id === data.trace_id;
            const shouldLoad = forgeStore.status !== 'complete' && !alreadyLoaded;
            if (shouldLoad) {
              import('$lib/api/client').then(({ getOptimization }) => {
                getOptimization(data.trace_id as string).then(opt => {
                  if (opt.status === 'completed' && forgeStore.status !== 'complete') {
                    forgeStore.loadFromRecord(opt);
                  }
                }).catch(() => {});
              });
            }
          }
          // F5: Propagate refinement turns to the refinement store for cross-tab sync
          if (type === 'refinement_turn') {
            const d = data as { optimization_id?: string };
            if (d.optimization_id && d.optimization_id === refinementStore.optimizationId) {
              refinementStore.reloadTurns(refinementStore.activeBranchId);
            }
          }
        }
        // F1: Route MCP pipeline progress through forgeStore.handleExternalEvent
        // instead of direct mutations — single code path for all SSE status events.
        if (type === 'optimization_status' || type === 'optimization_score_card' || type === 'optimization_start') {
          forgeStore.handleExternalEvent(type, data as Record<string, unknown>);
        }
        if (type === 'optimization_failed') {
          window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
          addToast('deleted', (data.error as string) || 'Optimization failed');
        }
        if (type === 'feedback_submitted') {
          window.dispatchEvent(new CustomEvent('feedback-event', { detail: data }));
          // Inline update in Navigator handles feedback_rating per-row —
          // no need for full history re-fetch via optimization-event.
        }
        if (type === 'strategy_changed') {
          window.dispatchEvent(new CustomEvent('strategy-changed', { detail: data }));
        }
        if (type === 'taxonomy_changed') {
          clustersStore.invalidateClusters();
          domainStore.invalidate();
          readinessStore.invalidate();
          addToast('created', 'Taxonomy updated');
        }
        if (type === 'taxonomy_activity') {
          clustersStore.pushActivityEvent(data as unknown as import('$lib/api/clusters').TaxonomyActivityEvent);
          // Candidate lifecycle toasts
          const actData = data as { op?: string; decision?: string; context?: Record<string, unknown> };
          if (actData.op === 'candidate') {
            const ctx = actData.context ?? {};
            if (actData.decision === 'candidate_promoted') {
              addToast('created', `Promoted: ${ctx.cluster_label ?? 'cluster'} → active`);
            }
            if (actData.decision === 'candidate_rejected') {
              const coh = typeof ctx.coherence === 'number' ? ` (coh ${ctx.coherence.toFixed(2)})` : '';
              const count = typeof ctx.member_count === 'number' ? ` — ${ctx.member_count} members reassigned` : '';
              addToast('deleted', `Rejected: ${ctx.cluster_label ?? 'cluster'}${coh}${count}`);
            }
          }
          if (actData.op === 'split' && actData.decision === 'split_complete') {
            const ctx = actData.context ?? {};
            if (ctx.children_state === 'candidate') {
              const childCount = typeof ctx.hdbscan_clusters === 'number' ? ctx.hdbscan_clusters : '?';
              addToast('created', `Split: ${childCount} candidates from ${ctx.parent_label ?? 'cluster'}`);
            }
          }
        }
        if (type === 'seed_batch_progress') {
          // F8: Persist seed batch progress in store (survives modal close)
          clustersStore.updateSeedProgress(data as { phase?: string; completed?: number; total?: number; current_prompt?: string });
          // Dispatch as a DOM custom event so SeedModal can listen
          // without being coupled to the SSE layer
          window.dispatchEvent(new CustomEvent('seed-batch-progress', { detail: data }));
        }
        if (type === 'agent_changed') {
          // Seed agent files were hot-reloaded — notify SeedModal to refresh agent list
          window.dispatchEvent(new CustomEvent('agent-changed', { detail: data }));
        }
        if (type === 'update_available') {
          updateStore.receive(data as Record<string, unknown>);
          addToast('modified', `Update available: v${(data as Record<string, unknown>).latest_version}`);
        }
        if (type === 'update_complete') {
          updateStore.receiveComplete(data as Record<string, unknown>);
        }
        if (type === 'domain_created') {
          domainStore.invalidate();
          readinessStore.invalidate();
        }
        if (type === 'domain_readiness_changed') {
          // `dispatchReadinessCrossing` checks two independent opt-outs (see
          // `readiness-notifications.svelte.ts`):
          //   1. `domain_readiness_notifications.enabled` — master toggle,
          //      flipped via the bell in DomainReadinessPanel header.
          //      Defaults to `true` (backend preferences.py DEFAULTS).
          //   2. `muted_domain_ids` — per-row opt-outs set by the per-domain
          //      bells. Survive master-mute toggles intentionally.
          // Both gates are read from the live preferences snapshot, so a
          // toggle flipped mid-session takes effect on the NEXT crossing.
          dispatchReadinessCrossing(data as unknown as ReadinessCrossingPayload);
          // Refetch reports so consumers (topology rings, readiness panel,
          // sparklines via invalidationEpoch) reflect the new tier without
          // waiting for the next taxonomy_changed event or manual refresh.
          // `invalidate()` is a fire-and-forget refetch guarded by a
          // generation counter — no infinite-loop risk because the backend
          // emits this event on tier crossings, not on every report fetch.
          readinessStore.invalidate();
        }
        if (type === 'routing_state_changed') {
          const d = data as { trigger?: string; provider: string | null; sampling_capable: boolean | null; mcp_connected: boolean; available_tiers: string[] };
          const wasSamplingCapable = forgeStore.samplingCapable === true;
          const prevTier = routing.tier;
          const delta = forgeStore.updateRoutingState({
            sampling_capable: d.sampling_capable,
            mcp_disconnected: !d.mcp_connected,
            provider: d.provider,
          });

          // Auto-enable force_sampling when sampling becomes available
          if (delta.samplingChanged) {
            onSamplingDetected();
            if (!preferencesStore.pipeline.force_sampling) {
              // Await the toggle so routing.tier reflects sampling BEFORE
              // triggering the guide. Without await, the guide reads the stale
              // tier (internal) because the preference hasn't persisted yet.
              preferencesStore.setPipelineToggle('force_sampling', true).then(() => {
                triggerTierGuide(routing.tier);
              });
            } else {
              triggerTierGuide(routing.tier);
            }
          }

          // Auto-disable force_sampling INSTANTLY when sampling goes away.
          // Optimistic local update first to prevent UI flash.
          if (wasSamplingCapable && d.sampling_capable !== true && preferencesStore.pipeline.force_sampling) {
            preferencesStore.prefs.pipeline.force_sampling = false;
            preferencesStore.setPipelineToggle('force_sampling', false);
          }

          if (delta.reconnected) addToast('created', 'MCP client reconnected');
          if (delta.disconnected && !forgeStore.provider) addToast('deleted', 'MCP client disconnected');

          // Only trigger tier guide when the effective tier actually CHANGED.
          // Without this guard, startup provider_changed events and benign
          // routing broadcasts (e.g. during recluster) pop the internal
          // pipeline modal even though the tier hasn't changed.
          if (!delta.samplingChanged && routing.tier !== prevTier) {
            triggerTierGuide(routing.tier);
          }
        }
        if (type === 'preferences_changed') {
          preferencesStore.prefs = data as unknown as Preferences;
        }
      },
      // onReconnect — refetch critical state after SSE recovery.
      () => {
        healthPoll();
        clustersStore.invalidateClusters();
        domainStore.invalidate();
        readinessStore.invalidate();
        window.dispatchEvent(new CustomEvent('strategy-changed'));
      },
    );

    const handleLoadOpt = (e: Event) => {
      const traceId = (e as CustomEvent).detail?.trace_id;
      if (traceId) {
        import('$lib/api/client').then(({ getOptimization }) => {
          getOptimization(traceId as string).then(opt => {
            if (opt) forgeStore.loadFromRecord(opt);
          }).catch(() => {});
        });
      }
    };
    window.addEventListener('load-optimization', handleLoadOpt);

    return () => {
      sseHealthStore.disconnect();
      window.removeEventListener('load-optimization', handleLoadOpt);
    };
  });

  // ---- Health polling (fixed 60s interval) ----

  const POLL_INTERVAL = 60_000;

  function healthPoll() {
    getHealth()
      .then(applyHealth)
      .catch(() => {
        if (!updateStore.updating) {
          backendError = 'Cannot connect to backend. Check that services are running.';
        }
      });
  }

  function applyHealth(h: HealthResponse) {
    backendError = null;
    const delta = forgeStore.updateRoutingState({
      sampling_capable: h.sampling_capable ?? null,
      mcp_disconnected: h.mcp_disconnected ?? false,
      provider: h.provider ?? null,
      version: h.version ?? null,
    });
    forgeStore.recentErrors = h.recent_errors ?? null;
    forgeStore.avgDurationMs = h.avg_duration_ms ?? null;
    forgeStore.scoreHealth = h.score_health ?? null;
    forgeStore.phaseDurations = (h.phase_durations && Object.keys(h.phase_durations).length > 0) ? h.phase_durations : null;
    forgeStore.domainCount = h.domain_count ?? null;
    forgeStore.domainCeiling = h.domain_ceiling ?? null;
    if (!firstHealthReceived) {
      firstHealthReceived = true;
      // Defer ALL toggle auto-sync and guide trigger until preferences load.
      // Otherwise we'd read/write stale defaults, and init() would overwrite
      // our patches when it resolves.
      pendingGuide = true;
      pendingHealthDelta = { health: h, delta };
    } else if (!preferencesStore.loading) {
      // Subsequent health polls (every 60s) — preferences already loaded,
      // safe to auto-sync toggles immediately.
      reconcileToggles(h, delta);
    }
  }

  // Initial poll + fixed interval
  $effect(() => {
    healthPoll();
    domainStore.load();
    updateStore.load();
    const timer = setInterval(healthPoll, POLL_INTERVAL);
    return () => clearInterval(timer);
  });

  // Gate: process toggle reconciliation AND trigger tier guide only after BOTH
  // health AND preferences have loaded. This prevents:
  // 1. Reading stale default preferences for toggle decisions
  // 2. init() overwriting toggle patches when it resolves
  // 3. Showing the wrong onboarding modal
  $effect(() => {
    if (pendingGuide && !preferencesStore.loading) {
      // First: reconcile toggles with actual capabilities
      if (pendingHealthDelta) {
        reconcileToggles(pendingHealthDelta.health, pendingHealthDelta.delta);
        pendingHealthDelta = null;
      }
      // Then: trigger guide. triggerTierGuide handles startup settle
      // internally (2s delay for MCP capability negotiation, cancelled
      // if a routing_state_changed SSE arrives first).
      pendingGuide = false;
      triggerTierGuide(routing.tier);
    }
  });

  // Derived error states
  let showRateLimit = $derived(forgeStore.error?.includes('Rate limit'));
  let showForgeError = $derived(
    forgeStore.status === 'error' && forgeStore.error && !showRateLimit
  );
</script>

<!-- Error Banners -->
{#if backendError}
  <div class="error-banner error-critical">
    <span>{backendError}</span>
    <button onclick={() => location.reload()}>Retry</button>
  </div>
{/if}

{#if showRateLimit}
  <div class="error-banner error-warning">
    <span>Rate limit reached. Try again in a moment.</span>
    <button onclick={() => forgeStore.error = null}>Dismiss</button>
  </div>
{/if}

{#if showForgeError}
  <div class="error-banner error-critical">
    <span>Optimization failed: {forgeStore.error}</span>
    <button onclick={() => { forgeStore.error = null; forgeStore.status = 'idle'; }}>Dismiss</button>
  </div>
{/if}

<!-- Main Editor -->
<EditorGroups />

<style>
  .error-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    font-size: 11px;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
  }

  .error-critical {
    border: 1px solid var(--color-neon-red);
    background: rgba(255, 51, 102, 0.06);
  }

  .error-warning {
    border: 1px solid var(--color-neon-yellow);
    background: rgba(251, 191, 36, 0.06);
  }

  .error-banner button {
    font-size: 10px;
    padding: 2px 8px;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    font-family: var(--font-sans);
  }

  .error-banner button:hover {
    border-color: var(--color-border-accent);
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
  }
</style>
