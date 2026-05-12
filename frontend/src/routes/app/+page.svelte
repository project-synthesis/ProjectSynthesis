<script lang="ts">
  import EditorGroups from '$lib/components/layout/EditorGroups.svelte';
  import RateLimitBanner from '$lib/components/shared/RateLimitBanner.svelte';
  import { rateLimitStore } from '$lib/stores/rate-limit.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { domainStore } from '$lib/stores/domains.svelte';
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import type { Preferences } from '$lib/stores/preferences.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getHealth } from '$lib/api/client';
  import type { HealthResponse } from '$lib/api/client';
  import { triggerTierGuide } from '$lib/stores/tier-onboarding.svelte';
  import { routing, type EffectiveTier } from '$lib/stores/routing.svelte';
  import { updateStore } from '$lib/stores/update.svelte';
  import { suitesStore } from '$lib/stores/suites.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import { sseHealthStore } from '$lib/stores/sse-health.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import {
    dispatchReadinessCrossing,
    type ReadinessCrossingPayload,
  } from '$lib/stores/readiness-notifications.svelte';

  let backendError = $state<string | null>(null);
  // ROOT CAUSE FIX (2026-05-09 cold-boot trigger): ``firstHealthReceived``
  // must be ``$state`` so the tier-watching ``$effect`` below subscribes
  // to its flip. Pre-fix it was a plain ``let`` — flipping it inside
  // ``applyHealth`` mutated the value but did NOT trigger Svelte 5
  // reactivity, so the cold-boot effect's early-return path was a
  // permanent dead-end on the very first run (the effect never read
  // ``routing.tier`` past the gate, so it never subscribed to tier
  // changes either). Promoting to ``$state`` plus reading ``routing.tier``
  // before the gate (see ``$effect`` at line ~457) restores the trigger.
  let firstHealthReceived = $state(false);
  // ROOT CAUSE FIX (2026-05-09): ``pendingGuide`` and ``pendingHealthDelta``
  // were plain ``let`` bindings — Svelte 5 only tracks ``$state`` reads as
  // reactive dependencies. The cold-boot $effect at line ~425 reads BOTH
  // ``pendingGuide`` AND ``preferencesStore.loading``, but only the latter
  // re-fired the effect on change. If preferences finished loading BEFORE
  // the first health poll resolved, the effect ran once with
  // ``pendingGuide=false`` (returning early), and never re-ran when
  // healthPoll later set ``pendingGuide=true`` (no reactive trigger). Cold
  // boot silently lost the auto-trigger; sampling→internal mid-session
  // worked because it goes through the SSE handler (event-driven, not
  // effect-driven). Promoting both to ``$state`` fixes the race.
  let pendingGuide = $state(false);
  let pendingHealthDelta = $state<{ health: HealthResponse; delta: any } | null>(null);

  /**
   * Reconcile force toggles with actual system capabilities.
   * Called after BOTH health and preferences are loaded to avoid races.
   *
   * Async (2026-05-09): callers MUST await before reading ``routing.tier``
   * to make a guide-trigger decision — pre-fix the function fired
   * ``setPipelineToggle`` (which only mutates ``prefs`` after the network
   * round-trip resolves) without awaiting, so the cold-boot ``pendingGuide``
   * effect read a stale tier (``internal``) and triggered InternalGuide
   * even when sampling was about to take over. Worse: when the sampling
   * tier later resolved, NO further trigger fired (the routing_state_changed
   * handler runs only on backend state changes, not preference flips), so
   * SamplingGuide silently never opened. Conversely on VS-Code-closed boot,
   * the same race could resolve to a flap that prevented InternalGuide
   * from opening at all. Awaiting the toggle fixes both directions.
   */
  async function reconcileToggles(h: HealthResponse, delta: any): Promise<void> {
    // Sampling just became available → auto-enable force_sampling, clear force_passthrough
    if (delta.samplingChanged) {
      onSamplingDetected();
      if (!preferencesStore.pipeline.force_sampling) {
        await preferencesStore.setPipelineToggle('force_sampling', true);
      }
    }
    // Sampling no longer available → clear stale force_sampling instantly
    // (optimistic local update BEFORE async API call to prevent UI flash)
    if (preferencesStore.pipeline.force_sampling && h.sampling_capable !== true) {
      const prev = preferencesStore.prefs.pipeline.force_sampling;
      preferencesStore.prefs.pipeline.force_sampling = false;
      await preferencesStore.setPipelineToggle('force_sampling', false).catch(() => {
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
        // Rate-limit observability + graceful-degradation banner.
        // ``rate_limit_active`` fires when probe/seed batches detect a 429
        // and the pipeline falls back to passthrough mode. The banner
        // explains the degraded state and shows when full LLM mode resumes.
        // ``rate_limit_cleared`` fires when a successful LLM call lands
        // against a previously-limited provider.
        if (type === 'rate_limit_active') {
          rateLimitStore.applyActive(data as Parameters<typeof rateLimitStore.applyActive>[0]);
          // One-shot toast on first hit per provider so the user notices
          // even when scrolling through the workbench. Banner is the
          // persistent surface; this is a nudge.
          const provider = (data as { provider?: string }).provider || 'LLM provider';
          addToast('info', `${provider} rate-limited — running in passthrough mode`);
        }
        if (type === 'rate_limit_cleared') {
          rateLimitStore.applyCleared(data as Parameters<typeof rateLimitStore.applyCleared>[0]);
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
          // Observatory's pattern-density Heatmap + Timeline backfill
          // are also derived from the cluster tree — sub-domain
          // emergence/dissolution silently went stale on the heatmap
          // without these refresh calls (live regression: dissolved
          // ``embedding-health`` row persisted in the heatmap for hours
          // after the row was archived + GC'd from the DB).
          void observatoryStore.refreshPatternDensity();
          void observatoryStore.loadTimelineEvents();
          // Re-dispatch as a kebab-case DOM CustomEvent so Navigator +
          // suitesStore (v0.4.22 T2) consumers can refresh without taking
          // a direct dependency on the SSE pipeline. This is the canonical
          // bridge per Navigator.svelte:54 + suites.svelte.ts:167.
          window.dispatchEvent(new CustomEvent('taxonomy-changed', { detail: data }));
          addToast('created', 'Taxonomy updated');
        }
        if (type === 'optimization_deleted') {
          // Backend event name is snake_case; frontend CustomEvent is kebab.
          // Unlike optimization_created/analyzed (consolidated into the generic
          // 'optimization-event' above for full-list re-fetch), deletion gets
          // its own dedicated event — HistoryPanel needs the per-row `id` so it
          // can remove that row surgically without a round-trip.
          window.dispatchEvent(new CustomEvent('optimization-deleted', { detail: data }));
        }
        // Reactivity (2026-05-09): backend publishes ``optimization_updated``
        // (e.g. metadata edit, score recalc, refinement applied in-place) but
        // it was never wired here — the history list silently went stale until
        // a manual refresh. Route it through the same 'optimization-event' bus
        // so HistoryPanel surgically re-fetches the affected row.
        if (type === 'optimization_updated') {
          window.dispatchEvent(new CustomEvent('optimization-event', { detail: data }));
        }
        // Reactivity (2026-05-09): ``repo_unlinked`` was published but never
        // handled. The frontend used to wait for the cascading ``taxonomy_changed``
        // event to invalidate cluster trees, but during the brief race window
        // (unlink ⇒ taxonomy_changed propagates ~100ms later) the GitHubPanel
        // would render stale ``linkedRepo`` state. Surgical clear on the
        // dedicated event closes the gap.
        if (type === 'repo_unlinked') {
          githubStore.linkedRepo = null;
          githubStore.fileTree = [];
          githubStore.branches = [];
          githubStore.indexStatus = null;
          window.dispatchEvent(new CustomEvent('repo-unlinked', { detail: data }));
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
        if (type === 'update_step') {
          updateStore.receiveStep(data as Record<string, unknown>);
        }
        if (type === 'domain_created') {
          domainStore.invalidate();
          readinessStore.invalidate();
          // New domain shifts the heatmap row set — refresh.
          void observatoryStore.refreshPatternDensity();
        }
        if (type === 'index_phase_changed') {
          // Live per-phase state for the linked repo — updates connectionState
          // through githubStore.applyPhaseEvent() (reactive $state mutation).
          githubStore.applyPhaseEvent(data as {
            repo_full_name: string;
            branch: string;
            phase: string;
            status: string;
            files_seen: number;
            files_total: number;
            error?: string;
          });
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
          const delta = forgeStore.updateRoutingState({
            sampling_capable: d.sampling_capable,
            mcp_disconnected: !d.mcp_connected,
            provider: d.provider,
          });

          // Auto-enable force_sampling when sampling becomes available.
          // Tier-guide trigger lives in the single ``$effect`` watching
          // ``routing.tier`` below — no per-handler call here. Persisting
          // the toggle (sync or async) re-derives ``routing.tier`` and
          // the effect fires once, with the post-mutation tier.
          if (delta.samplingChanged) {
            onSamplingDetected();
            if (!preferencesStore.pipeline.force_sampling) {
              void preferencesStore.setPipelineToggle('force_sampling', true);
            }
          }

          // Auto-disable force_sampling INSTANTLY when sampling goes away.
          // Optimistic local update first to prevent UI flash.
          if (wasSamplingCapable && d.sampling_capable !== true && preferencesStore.pipeline.force_sampling) {
            preferencesStore.prefs.pipeline.force_sampling = false;
            void preferencesStore.setPipelineToggle('force_sampling', false);
          }

          if (delta.reconnected) addToast('created', 'MCP client reconnected');
          if (delta.disconnected && !forgeStore.provider) addToast('deleted', 'MCP client disconnected');
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
        // Observatory rebuild — taxonomy events that fired during the
        // disconnect window are gone; the only safe baseline is a re-fetch.
        void observatoryStore.refreshPatternDensity();
        void observatoryStore.loadTimelineEvents();
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
    
    // Sync persistent rate-limit state from backend
    if (h.rate_limit) {
      rateLimitStore.applyActive(h.rate_limit as any);
    } else if (h.rate_limit === null) {
      // If health reports null, clear any active limits to recover from stale frontend state
      for (const provider of rateLimitStore.active.keys()) {
        rateLimitStore.applyCleared({ provider });
      }
    }

    if (!firstHealthReceived) {
      firstHealthReceived = true;
      // Defer ALL toggle auto-sync and guide trigger until preferences load.
      // Otherwise we'd read/write stale defaults, and init() would overwrite
      // our patches when it resolves.
      pendingGuide = true;
      pendingHealthDelta = { health: h, delta };
    } else if (!preferencesStore.loading) {
      // Subsequent health polls (every 60s) — preferences already loaded,
      // safe to auto-sync toggles immediately. Fire-and-forget here is
      // OK because no guide-trigger reads ``routing.tier`` synchronously
      // off the back of the periodic poll; the cold-boot path that does
      // is in the $effect below.
      void reconcileToggles(h, delta);
    }
  }

  // Initial poll + fixed interval
  $effect(() => {
    healthPoll();
    domainStore.load();
    updateStore.load();
    // v0.4.22 T2: start the suites-store 30s alarm-block poll so the
    // RegressionBadge in StatusBar surfaces live regression state without
    // a navigator entry visit. Idempotent — handles HMR/re-mount cleanly.
    suitesStore.startPolling();
    const timer = setInterval(healthPoll, POLL_INTERVAL);
    return () => {
      clearInterval(timer);
      suitesStore.stopPolling();
    };
  });

  // Cold-boot toggle reconciliation. Auto-enables ``force_sampling`` on
  // first health response when sampling is detected, or auto-disables a
  // stale ``force_sampling`` when sampling is gone. Decoupled from the
  // tier-guide trigger as of 2026-05-09: that pathway is now driven by
  // the single ``$effect`` below that watches ``routing.tier`` directly.
  $effect(() => {
    if (!pendingGuide || preferencesStore.loading) return;
    const captured = pendingHealthDelta;
    pendingGuide = false;
    pendingHealthDelta = null;
    if (captured) {
      void reconcileToggles(captured.health, captured.delta);
    }
  });

  // Single source of truth for tier-driven onboarding-guide triggers.
  //
  // Pre-2026-05-09 design: five separate call sites — cold-boot effect,
  // SSE handler (samplingChanged branch), SSE handler (tier-changed
  // branch), Settings toggle force_sampling, Settings toggle
  // force_passthrough — each calling ``triggerTierGuide(routing.tier)``
  // at slightly different moments. The fragmentation produced bug after
  // bug: stale tier reads (await/no-await drift), missed transitions
  // (preferences_changed never triggered), missed rate-limit flips,
  // module-level ``lastTriggeredTier`` HMR corruption, etc.
  //
  // Post-fix design: ONE effect watches ``routing.tier``. Every pathway
  // that flips the tier (SSE event, preference toggle, optimistic
  // mutation, rate-limit transition, preferences_changed cross-client
  // broadcast) flows through one of ``routing.tier``'s reactive
  // dependencies, so the effect re-runs once with the genuine post-flip
  // tier. The Settings-toggle ``show(false)`` calls remain in
  // SettingsPanel for explicit user opt-in (force-open ignoring
  // dismissal); they don't go through this effect.
  //
  // Bootstrap gate: wait until BOTH first health response AND
  // preferences have settled — the very first run reports the
  // genuine post-boot tier, not the initial 'passthrough' default.
  let lastEffectiveTier = $state<EffectiveTier | null>(null);
  $effect(() => {
    // CRITICAL: read ``routing.tier`` BEFORE the early-return gate so
    // Svelte 5's dep tracker subscribes the effect to tier changes on
    // EVERY run. Pre-fix the gate fired on a still-loading boot, the
    // function returned before line 4, and the effect was permanently
    // unsubscribed from ``routing.tier``. Subsequent tier changes
    // (provider detection, preference toggle, rate-limit flip) never
    // re-fired the effect → cold-boot guides silently never opened.
    const tier = routing.tier;
    if (!firstHealthReceived || preferencesStore.loading) return;
    if (tier === lastEffectiveTier) return;
    lastEffectiveTier = tier;
    triggerTierGuide(tier);
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

<!-- Rate-limit banner: shown when ANY LLM provider is currently rate-limited.
     Mounted above the main editor so it's visible regardless of which tab
     is active. Auto-clears via rateLimitStore's per-second tick. -->
<RateLimitBanner />

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
