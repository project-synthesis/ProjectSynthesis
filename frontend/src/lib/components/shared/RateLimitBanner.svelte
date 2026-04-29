<!--
  RateLimitBanner — global banner shown when any LLM provider is rate-limited.

  Consumes ``rateLimitStore.activeList`` and renders a thin amber-tinted
  strip across the top of the workbench. Dismissible per-session (an
  ``aria-live`` polite region announces the limit; ``Esc`` collapses to a
  compact tier badge in the StatusBar instead).

  Brand: industrial cyberpunk — 1px amber contour, no rounded corners,
  no shadows. The countdown ticks every second from rateLimitStore's
  shared $now cell.
-->
<script lang="ts">
    import { rateLimitStore } from '$lib/stores/rate-limit.svelte';

    let dismissed = $state(false);

    const active = $derived(rateLimitStore.activeList);
    const visible = $derived(rateLimitStore.isAnyActive && !dismissed);

    function formatCountdown(seconds: number | null): string {
        if (seconds == null) return 'unknown';
        if (seconds < 60) return `${seconds}s`;
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        if (m < 60) return `${m}m ${s}s`;
        const h = Math.floor(m / 60);
        return `${h}h ${m % 60}m`;
    }

    function providerLabel(p: string): string {
        // Plan-agnostic label: the Claude CLI provider works against any
        // Anthropic plan (Pro / Team / Enterprise / MAX / Bedrock / Vertex).
        // Don't bake a specific plan name into UI labels -- the rate-limit
        // can come from any of them.
        switch (p) {
            case 'claude_cli':
                return 'Claude CLI';
            case 'anthropic_api':
                return 'Anthropic API';
            default:
                return p;
        }
    }

    // Re-show banner when a NEW provider gets rate-limited after dismissal
    let lastDismissedAt = $state<number | null>(null);
    $effect(() => {
        if (!rateLimitStore.isAnyActive) {
            dismissed = false;
            lastDismissedAt = null;
        } else if (lastDismissedAt && active.length > 0) {
            // Re-show if a fresh limit fired after dismissal.
            const newest = Math.max(0, ...active.map(() => Date.now()));
            if (newest > lastDismissedAt + 1000) dismissed = false;
        }
    });
</script>

{#if visible}
    <div
        role="status"
        aria-live="polite"
        class="rate-limit-banner border-b border-amber-500/40 bg-amber-500/[.06] px-4 py-2 text-xs"
    >
        <div class="flex items-center gap-3">
            <span class="font-mono uppercase tracking-wider text-amber-400">
                ⏸ Rate-limited
            </span>
            {#each active as entry (entry.provider)}
                <span class="text-zinc-300">
                    {providerLabel(entry.provider)} —
                    {#if entry.seconds_remaining != null}
                        full LLM mode resumes in
                        <span class="font-mono text-amber-300">
                            {formatCountdown(entry.seconds_remaining)}
                        </span>
                        {#if entry.reset_at_iso}
                            ({new Date(entry.reset_at_iso).toLocaleTimeString()})
                        {/if}
                    {:else}
                        retry shortly
                    {/if}
                </span>
            {/each}
            <span class="text-zinc-500">·</span>
            <span class="text-zinc-400">
                Currently running in
                <span class="font-mono text-zinc-200">passthrough</span>
                mode (heuristic-only). Probes &amp; seeds will resume LLM
                scoring automatically.
            </span>
            <button
                type="button"
                class="ml-auto text-zinc-500 hover:text-zinc-300"
                aria-label="Dismiss rate-limit banner"
                onclick={() => {
                    dismissed = true;
                    lastDismissedAt = Date.now();
                }}
            >
                ✕
            </button>
        </div>
    </div>
{/if}

<style>
    .rate-limit-banner {
        font-family:
            ui-sans-serif,
            system-ui,
            -apple-system,
            'Segoe UI',
            Roboto,
            sans-serif;
    }
</style>
