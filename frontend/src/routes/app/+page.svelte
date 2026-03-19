<script lang="ts">
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getHealth, connectEventStream } from '$lib/api/client';
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
      }
    });
    return () => eventSource?.close();
  });

  // Health polling — initial fast poll (10s) to detect MCP client connections
  // quickly, then settles to 60s after 2 minutes. The ASGI middleware writes
  // mcp_session.json on the MCP initialize handshake, so detection happens
  // within one poll interval of the client connecting.
  function applyHealth(h: HealthResponse) {
    health = h;
    backendError = null;
    forgeStore.noProvider = !h.provider;
    forgeStore.samplingCapable = h.sampling_capable ?? null;
  }

  $effect(() => {
    const FAST_INTERVAL = 10_000;   // 10s during startup window
    const SLOW_INTERVAL = 60_000;   // 60s steady-state
    const FAST_WINDOW   = 120_000;  // 2 min fast-poll window
    const startedAt = Date.now();

    const poll = () => {
      getHealth()
        .then(applyHealth)
        .catch(() => { backendError = 'Cannot connect to backend. Check that services are running.'; });
    };
    poll();

    // Start with fast polling, switch to slow after the fast window
    let timer = setInterval(poll, FAST_INTERVAL);
    const slowdown = setTimeout(() => {
      clearInterval(timer);
      timer = setInterval(poll, SLOW_INTERVAL);
    }, FAST_WINDOW);

    return () => {
      clearInterval(timer);
      clearTimeout(slowdown);
    };

    // GitHub auth checked lazily when user navigates to GitHub panel
    // (avoids 401 console noise on every page load when OAuth isn't configured)
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
