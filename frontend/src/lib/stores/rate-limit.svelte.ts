/**
 * Rate-limit state store (v0.4.12).
 *
 * Tracks active LLM-provider rate limits surfaced from the backend so the UI
 * can render a coherent global banner + per-tier badges + a Settings detail
 * card. Subscribes to two SSE events:
 *
 * - ``rate_limit_active`` — emitted when a probe/seed batch detects a 429
 *   from any provider. Payload: ``{ provider, reset_at_iso, estimated_wait_seconds }``.
 *   The store records the limit and starts a per-second countdown derived
 *   from ``reset_at_iso``. While a limit is active, downstream LLM calls
 *   automatically fall back to the passthrough tier (heuristic-only); the
 *   banner explains this and points the user at when full LLM mode resumes.
 *
 * - ``rate_limit_cleared`` — emitted when a successful LLM call lands
 *   against a previously-limited provider. Clears the corresponding entry.
 *
 * The store also auto-clears any limit whose ``reset_at_iso`` is in the
 * past on the next tick, in case the backend missed the cleared event
 * (e.g. process restart between rate-limit hit and reset).
 *
 * Brand: dark-mode banner uses warning-tinted accent (no full-bleed alarm
 * color); inline TierBadge gets a tiny ⏸ marker so users can tell which
 * tier is currently in passthrough fallback without a modal.
 */

interface RateLimitState {
    provider: string; // 'claude_cli' | 'anthropic_api' | etc
    reset_at_iso: string | null;
    estimated_wait_seconds: number | null;
    detected_at_ms: number; // wall-clock ms when this limit was first observed
    last_event_at_ms: number; // most recent rate_limit_active event
}

class RateLimitStore {
    /** Map of provider -> active rate-limit state. Null when no limit is active. */
    active = $state<Map<string, RateLimitState>>(new Map());

    /**
     * Wall-clock now-tick in ms. Updated every second so $derived countdowns
     * re-render. We use a single $state cell so all derived consumers share
     * the same tick (no per-component setInterval).
     */
    private _now = $state<number>(Date.now());
    private _tickHandle: ReturnType<typeof setInterval> | null = null;

    /** True when ANY provider is currently rate-limited. Drives the global banner. */
    isAnyActive = $derived.by(() => {
        if (this.active.size === 0) return false;
        // Force re-evaluation against the tick cell so newly-expired
        // entries fall out without an explicit clear event.
        const _ = this._now;
        for (const [, state] of this.active) {
            if (this._isStillActive(state, _)) return true;
        }
        return false;
    });

    /** List of currently active rate limits (for Settings UI rendering). */
    activeList = $derived.by(() => {
        const _ = this._now;
        const out: Array<{
            provider: string;
            reset_at_iso: string | null;
            seconds_remaining: number | null;
        }> = [];
        for (const [provider, state] of this.active) {
            if (!this._isStillActive(state, _)) continue;
            out.push({
                provider,
                reset_at_iso: state.reset_at_iso,
                seconds_remaining: this._secondsUntilReset(state, _),
            });
        }
        // Stable ordering: longest wait first.
        out.sort((a, b) =>
            (b.seconds_remaining ?? 0) - (a.seconds_remaining ?? 0),
        );
        return out;
    });

    /**
     * Apply a `rate_limit_active` SSE event payload.
     *
     * Idempotent — if the same provider fires multiple events in a batch
     * (e.g. all 5 probe prompts hit the same limit), we keep the latest
     * reset_at and the earliest detected_at.
     */
    applyActive(payload: {
        provider?: string;
        reset_at_iso?: string | null;
        estimated_wait_seconds?: number | null;
    }): void {
        const provider = payload.provider || 'unknown';
        const existing = this.active.get(provider);
        const next: RateLimitState = {
            provider,
            reset_at_iso: payload.reset_at_iso ?? null,
            estimated_wait_seconds: payload.estimated_wait_seconds ?? null,
            detected_at_ms: existing?.detected_at_ms ?? Date.now(),
            last_event_at_ms: Date.now(),
        };
        // Trigger reactivity by replacing the Map (Svelte 5 tracks
        // .set() on Map but reassignment is the safe public-API
        // pattern across all reactivity primitives).
        const updated = new Map(this.active);
        updated.set(provider, next);
        this.active = updated;
        this._ensureTicking();
    }

    /** Apply a `rate_limit_cleared` SSE event payload. */
    applyCleared(payload: { provider?: string }): void {
        const provider = payload.provider || 'unknown';
        if (!this.active.has(provider)) return;
        const updated = new Map(this.active);
        updated.delete(provider);
        this.active = updated;
        if (updated.size === 0) this._stopTicking();
    }

    /**
     * Hint from the backend when a probe/seed completed in passthrough
     * fallback mode without firing a separate rate_limit_active event
     * (e.g. on resume from a stale state). Shape: a partial payload from
     * an Optimization row's heuristic_flags.
     */
    applyHeuristicFlags(flags: Record<string, unknown> | null): void {
        if (!flags || flags.rate_limited !== true) return;
        this.applyActive({
            provider: typeof flags.provider === 'string' ? flags.provider : undefined,
            reset_at_iso: typeof flags.reset_at_iso === 'string' ? flags.reset_at_iso : null,
            estimated_wait_seconds:
                typeof flags.estimated_wait_seconds === 'number'
                    ? flags.estimated_wait_seconds
                    : null,
        });
    }

    /** Test helper: reset the store. Not part of the public API. */
    _reset(): void {
        this.active = new Map();
        this._stopTicking();
        this._now = Date.now();
    }

    private _isStillActive(state: RateLimitState, now: number): boolean {
        // No reset_at means we don't know -- err on the side of "still
        // active" for 5 minutes from detection so the banner doesn't
        // flicker. After 5 min without a fresh event, drop it.
        if (!state.reset_at_iso) {
            return now - state.detected_at_ms < 5 * 60 * 1000;
        }
        const reset = Date.parse(state.reset_at_iso);
        if (Number.isNaN(reset)) return now - state.detected_at_ms < 5 * 60 * 1000;
        return reset > now;
    }

    private _secondsUntilReset(
        state: RateLimitState,
        now: number,
    ): number | null {
        if (state.reset_at_iso) {
            const reset = Date.parse(state.reset_at_iso);
            if (!Number.isNaN(reset)) return Math.max(0, Math.round((reset - now) / 1000));
        }
        if (state.estimated_wait_seconds != null) {
            const elapsed = (now - state.detected_at_ms) / 1000;
            return Math.max(0, Math.round(state.estimated_wait_seconds - elapsed));
        }
        return null;
    }

    private _ensureTicking(): void {
        if (this._tickHandle != null) return;
        this._tickHandle = setInterval(() => {
            this._now = Date.now();
            // Auto-prune entries whose reset has elapsed -- guards
            // against a missed rate_limit_cleared event.
            let needsPrune = false;
            for (const [, state] of this.active) {
                if (!this._isStillActive(state, this._now)) {
                    needsPrune = true;
                    break;
                }
            }
            if (needsPrune) {
                const updated = new Map<string, RateLimitState>();
                for (const [k, v] of this.active) {
                    if (this._isStillActive(v, this._now)) updated.set(k, v);
                }
                this.active = updated;
                if (updated.size === 0) this._stopTicking();
            }
        }, 1000);
    }

    private _stopTicking(): void {
        if (this._tickHandle != null) {
            clearInterval(this._tickHandle);
            this._tickHandle = null;
        }
    }
}

export const rateLimitStore = new RateLimitStore();
