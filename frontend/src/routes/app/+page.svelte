<script lang="ts">
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import type { Preferences } from '$lib/stores/preferences.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getHealth, connectEventStream } from '$lib/api/client';
  import type { HealthResponse } from '$lib/api/client';

  let health = $state<HealthResponse | null>(null);
  let backendError = $state<string | null>(null);
  let eventSource: EventSource | null = null;
  let sseHadError = false;

  // Real-time event stream
  $effect(() => {
    eventSource = connectEventStream((type, data) => {
      if (type === 'optimization_created' || type === 'optimization_analyzed' || type === 'refinement_turn') {
        window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
        // Toast for optimizations not from the current UI session (e.g., MCP)
        const isOwnTrace = data.trace_id === forgeStore.traceId || data.trace_id === forgeStore.passthroughTraceId;
        if (type !== 'refinement_turn' && data.trace_id && !isOwnTrace) {
          const label = type === 'optimization_analyzed' ? 'analyzed' : 'optimized';
          addToast('created', `Prompt ${label}`);
        }
      }
      if (type === 'optimization_failed') {
        window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
        addToast('deleted', (data.error as string) || 'Optimization failed');
      }
      if (type === 'feedback_submitted') {
        window.dispatchEvent(new CustomEvent('feedback-event', { detail: data }));
        // Also trigger history refresh so feedback counts update
        window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
      }
      if (type === 'strategy_changed') {
        window.dispatchEvent(new CustomEvent('strategy-changed', { detail: data }));
      }
      if (type === 'taxonomy_changed') {
        clustersStore.invalidateClusters();
        addToast('created', 'Taxonomy updated');
      }
      if (type === 'routing_state_changed') {
        const d = data as { provider: string | null; sampling_capable: boolean | null; mcp_connected: boolean; available_tiers: string[] };
        const delta = forgeStore.updateRoutingState({
          sampling_capable: d.sampling_capable,
          mcp_disconnected: !d.mcp_connected && d.sampling_capable !== null,
          provider: d.provider,
        });
        if (delta.reconnected) addToast('created', 'MCP client reconnected');
        if (delta.samplingChanged) {
          addToast('created', 'MCP client connected with sampling capability');
          // Auto-clear force_passthrough so routing resolves to sampling
          if (preferencesStore.pipeline.force_passthrough) {
            preferencesStore.setPipelineToggle('force_passthrough', false);
          }
        }
        // Only toast on disconnect when no local provider (true degradation).
        // When CLI/API is available, the auto-fallback is silent.
        if (delta.disconnected && !forgeStore.provider) addToast('deleted', 'MCP client disconnected');
      }
      if (type === 'preferences_changed') {
        preferencesStore.prefs = data as unknown as Preferences;
      }
    });

    // SSE reconnection reconciliation — EventSource auto-reconnects on error,
    // but events may have been lost during the gap. The server sends replays
    // via Last-Event-ID, but as a defense-in-depth we also refetch critical
    // state when the connection recovers.
    eventSource.addEventListener('open', () => {
      if (sseHadError) {
        sseHadError = false;
        healthPoll();
        clustersStore.invalidateClusters();
        window.dispatchEvent(new CustomEvent('strategy-changed'));
      }
    });
    eventSource.onerror = () => {
      sseHadError = true;
    };

    return () => eventSource?.close();
  });

  // ---- Health polling (fixed 60s interval) ----

  const POLL_INTERVAL = 60_000;

  function healthPoll() {
    getHealth()
      .then(applyHealth)
      .catch(() => { backendError = 'Cannot connect to backend. Check that services are running.'; });
  }

  function applyHealth(h: HealthResponse) {
    health = h;
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
    if (delta.samplingChanged) {
      addToast('created', 'MCP client connected with sampling capability');
      // Auto-clear force_passthrough so routing resolves to sampling
      if (preferencesStore.pipeline.force_passthrough) {
        preferencesStore.setPipelineToggle('force_passthrough', false);
      }
    }
  }

  // Initial poll + fixed interval
  $effect(() => {
    healthPoll();
    const timer = setInterval(healthPoll, POLL_INTERVAL);
    return () => clearInterval(timer);
  });

  // Derived error states
  let showNoProvider = $derived(health && !health.provider && !backendError);
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

{#if showNoProvider}
  <div class="error-banner error-warning">
    <div class="banner-text">
      <span>No provider configured — using manual passthrough mode.</span>
      <span class="banner-subtitle">Prompts will be assembled for external LLM processing. Add an API key in Settings for full automation.</span>
    </div>
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

  .banner-text {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .banner-subtitle {
    font-size: 10px;
    color: var(--color-text-dim);
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
