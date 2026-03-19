<script lang="ts">
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getHealth, getOptimization, connectEventStream } from '$lib/api/client';
  import type { HealthResponse } from '$lib/api/client';

  let health = $state<HealthResponse | null>(null);
  let backendError = $state<string | null>(null);
  let eventSource: EventSource | null = null;

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
      if (type === 'pattern_updated') {
        patternsStore.invalidateGraph();
        addToast('created', `Pattern family updated: ${(data.intent_label as string) || 'new family'}`);

        // Live family link: if the extractor just linked the current result to a family,
        // refresh to pick up the family_id so Inspector auto-shows the family detail.
        const eventOptId = data.optimization_id as string | undefined;
        if (
          eventOptId &&
          forgeStore.status === 'complete' &&
          forgeStore.result?.id === eventOptId
        ) {
          getOptimization(forgeStore.result.trace_id)
            .then((updated) => forgeStore.loadFromRecord(updated))
            .catch(() => { /* best-effort refresh */ });
        }
      }
      if (type === 'mcp_session_changed') {
        if (data.disconnected) {
          // SSE stream closed — client disconnected. Apply immediately
          // without waiting for the next health poll.
          forgeStore.mcpDisconnected = true;
          if (!preferencesStore.pipeline.force_passthrough) {
            preferencesStore.update({ pipeline: { force_passthrough: true, auto_passthrough: true } });
            addToast('deleted', 'MCP client disconnected — switched to passthrough mode');
          }
        } else {
          // MCP client connected/reconnected — trigger immediate health poll
          getHealth().then(applyHealth).catch(() => {});
        }
      }
    });
    return () => eventSource?.close();
  });

  // Health polling — initial fast poll (10s) to detect MCP client connections
  // quickly, then settles to 60s after 2 minutes. The ASGI middleware writes
  // mcp_session.json on the MCP initialize handshake, so detection happens
  // within one poll interval of the client connecting.
  function applyHealth(h: HealthResponse) {
    const prevSampling = forgeStore.samplingCapable;
    const prevDisconnected = forgeStore.mcpDisconnected;
    health = h;
    backendError = null;
    forgeStore.noProvider = !h.provider;
    forgeStore.samplingCapable = h.sampling_capable ?? null;
    forgeStore.mcpDisconnected = h.mcp_disconnected ?? false;

    // Guard: clear stale force_sampling when no sampling client is available.
    // This catches the cold-start case where preferences persist force_sampling=true
    // from a previous session but the IDE is no longer connected.
    if (preferencesStore.pipeline.force_sampling && h.sampling_capable !== true) {
      preferencesStore.setPipelineToggle('force_sampling', false);
      addToast('deleted', 'No sampling-capable MCP client — force sampling disabled');
    }

    // Guard: clear stale auto_passthrough when no sampling client exists.
    // On cold start after a server restart, mcp_session.json is cleared so
    // sampling_capable=null. If auto_passthrough persists from a previous
    // disconnect, clear both flags so the system defaults to internal pipeline.
    if (preferencesStore.pipeline.auto_passthrough && h.sampling_capable === null) {
      preferencesStore.update({ pipeline: { force_passthrough: false, auto_passthrough: false } });
      forgeStore.mcpDisconnected = false;
      addToast('created', 'Auto-passthrough cleared — no MCP client session');
    }

    // Toast when sampling capability first detected
    if (prevSampling !== true && h.sampling_capable === true) {
      addToast('created', 'MCP client connected with sampling capability');
    }

    // Auto-switch to passthrough on MCP disconnect
    if (!prevDisconnected && h.mcp_disconnected === true && !preferencesStore.pipeline.force_passthrough) {
      preferencesStore.update({ pipeline: { force_passthrough: true, auto_passthrough: true } });
      addToast('deleted', 'MCP client disconnected — switched to passthrough mode');
    }

    // Auto-restore from passthrough on MCP reconnect
    if (prevDisconnected && !h.mcp_disconnected && h.sampling_capable === true && preferencesStore.pipeline.auto_passthrough) {
      preferencesStore.update({ pipeline: { force_passthrough: false, auto_passthrough: false } });
      addToast('created', 'MCP client reconnected — restored sampling mode');
    }
  }

  // Health polling — fast 10s during startup + whenever a sampling client is
  // connected (for disconnect detection), then 60s steady-state otherwise.
  let healthTimer: ReturnType<typeof setInterval> | null = null;
  let healthSlowdown: ReturnType<typeof setTimeout> | null = null;
  const FAST_INTERVAL = 10_000;
  const SLOW_INTERVAL = 60_000;
  const FAST_WINDOW   = 120_000;

  function healthPoll() {
    getHealth()
      .then(applyHealth)
      .catch(() => { backendError = 'Cannot connect to backend. Check that services are running.'; });
  }

  function setHealthInterval(ms: number) {
    if (healthTimer !== null) clearInterval(healthTimer);
    healthTimer = setInterval(healthPoll, ms);
  }

  $effect(() => {
    healthPoll();

    // Start with fast polling, switch to slow after the fast window
    setHealthInterval(FAST_INTERVAL);
    healthSlowdown = setTimeout(() => {
      // Only slow down if no sampling client is active — keep fast for disconnect detection
      if (forgeStore.samplingCapable !== true) {
        setHealthInterval(SLOW_INTERVAL);
      }
    }, FAST_WINDOW);

    return () => {
      if (healthTimer !== null) clearInterval(healthTimer);
      if (healthSlowdown !== null) clearTimeout(healthSlowdown);
    };
  });

  // Keep fast polling while a sampling-capable client is connected (for disconnect detection).
  // When sampling goes away, switch to slow polling.
  let prevSamplingForPoll = $state<boolean | null>(null);
  $effect(() => {
    const current = forgeStore.samplingCapable;
    if (prevSamplingForPoll !== true && current === true) {
      setHealthInterval(FAST_INTERVAL);
    } else if (prevSamplingForPoll === true && current !== true) {
      setHealthInterval(SLOW_INTERVAL);
    }
    prevSamplingForPoll = current;
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
