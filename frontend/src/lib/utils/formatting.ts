/**
 * Shared formatting utilities for scores, numbers, and text display.
 */

/** Format a numeric score for display (e.g. 7.3). Returns '--' for null/undefined/NaN/Infinity. */
export function formatScore(score: number | null | undefined, decimals = 1): string {
  if (score == null || !isFinite(score)) return '--';
  return score.toFixed(decimals);
}

/** Format a delta value with sign prefix (e.g. '+1.2', '-0.3'). */
export function formatDelta(delta: number, decimals = 1): string {
  return (delta > 0 ? '+' : '') + delta.toFixed(decimals);
}

/**
 * Format a signed delta with Unicode U+2212 minus (canonical "chromatic
 * minus" per SKILL.md numeric voice). Null-safe — returns the placeholder
 * `—` (U+2014 em-dash) for null/undefined. Below the epsilon zone (default
 * `0.005`) the delta is rendered as `0.NN` without a sign, so display
 * doesn't show `+0.00` / `−0.00` for jitter-class values.
 *
 * Used by the Topic Probe Tier 2 suite surfaces (SuiteRow + SuiteDetailView)
 * where signed deltas vs baseline are surfaced inline; the scoring
 * convention shows two decimals (`−0.64 vs baseline` per spec § 6 voice
 * table).
 */
export function formatSignedDelta(
  delta: number | null | undefined,
  decimals = 2,
  epsilon = 0.005,
): string {
  if (delta == null || !isFinite(delta)) return '—';
  const abs = Math.abs(delta).toFixed(decimals);
  if (delta > epsilon) return `+${abs}`;
  if (delta < -epsilon) return `−${abs}`;
  return (0).toFixed(decimals);
}

/** Truncate text to maxLen characters, appending '...' if truncated. Returns '' for null/undefined. */
export function truncateText(text: string | null | undefined, maxLen = 80): string {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen).trimEnd() + '...';
}

/**
 * Relative-time formatter — single source of truth across the workbench.
 *
 * Default (`opts.suffix === 'compact'` or omitted) returns the IDE-density
 * form `2h`, `3d`, `1mo` for sidebar rows + tables. `opts.suffix === 'ago'`
 * adds the natural-language tail `2h ago`, `3d ago`, `just now` for
 * conversational rows in cards + tooltips.
 *
 * Accepts either an ISO string OR a numeric epoch-ms timestamp — the rate-
 * limit accordion in SettingsPanel previously hand-rolled its own
 * `formatDetectedAgo(detected_at_ms: number)` because the original signature
 * was string-only. The narrowed overload absorbs that consumer without
 * losing the ISO path used by HistoryPanel / GitHubPanel / Inspector.
 *
 * Pre-v0.4.39 a sibling `formatTimeAgo` lived in `utils/taxonomy-health.ts`
 * with subtly different break points; both have been collapsed here.
 */
export function formatRelativeTime(
  input: string | number,
  opts: { suffix?: 'ago' | 'compact' } = {},
): string {
  const ms = typeof input === 'number' ? input : new Date(input).getTime();
  const diff = Date.now() - ms;
  const suffix = opts.suffix ?? 'compact';
  if (diff < 0) return suffix === 'ago' ? 'just now' : 'now';
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return suffix === 'ago' ? 'just now' : 'now';
  const tail = suffix === 'ago' ? ' ago' : '';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m${tail}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h${tail}`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d${tail}`;
  if (days < 30) return `${Math.floor(days / 7)}w${tail}`;
  if (days < 365) return `${Math.floor(days / 30)}mo${tail}`;
  return `${Math.floor(days / 365)}y${tail}`;
}

/** Trend threshold (absolute) below which the trend is considered stable. */
const TREND_THRESHOLD = 0.1;

/** Classify a trend value into label, color, and char for consistent display. */
export function trendInfo(trend: number): { label: string; color: string; char: string } {
  if (trend > TREND_THRESHOLD) {
    return { label: 'improving', color: 'var(--color-neon-green)', char: '/' };
  }
  if (trend < -TREND_THRESHOLD) {
    return { label: 'declining', color: 'var(--color-neon-red)', char: '\\' };
  }
  return { label: 'stable', color: 'var(--color-text-dim)', char: '-' };
}

/** Extract primary domain from "primary: qualifier" format, lowercased. */
export function parsePrimaryDomain(domain: string | null | undefined): string {
  if (!domain) return 'general';
  const idx = domain.indexOf(':');
  return (idx >= 0 ? domain.substring(0, idx).trim() : domain.trim()).toLowerCase() || 'general';
}

/** Format a character count as compact "K" string (e.g. 27286 -> "27.3K"). */
export function formatCompactChars(chars: number): string {
  if (chars < 1000) return String(chars);
  return (chars / 1000).toFixed(1) + 'K';
}

/** True when the result was produced via a passthrough flow (web or MCP). */
export function isPassthroughResult(result: { provider?: string } | null | undefined): boolean {
  return result?.provider?.endsWith('_passthrough') === true;
}

/**
 * Copy text to clipboard with fallback for older browsers.
 * Returns true on success, false on failure.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback: temporary textarea + execCommand
    const el = document.createElement('textarea');
    el.value = text;
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(el);
    return ok;
  }
}

/**
 * Map a `replay_warnings` code to a `{short, description}` pair.
 *
 * `short` is the chip label rendered in the warnings strip (3–8 chars,
 * lower-case noun, mono-friendly). `description` is the long-form prose
 * surfaced in the chip tooltip + announced via `aria-live` so screen
 * readers report what an opaque code means.
 *
 * Unknown codes degrade safely — the raw code becomes the short label and
 * the description is a generic "unknown warning" message. This keeps the
 * helper forward-compatible with backend warning codes that ship before a
 * UI mapping lands (R-26 v0.4.39 brand-compliance).
 *
 * Backend emit sites today (search: `warnings.append`):
 *   - `repo_drift` — suite + current repo differ; replay still runs.
 */
export function warningCodeLabel(code: string): { short: string; description: string } {
  switch (code) {
    case 'repo_drift':
      return {
        short: 'repo drift',
        description: 'Suite repo and current repo differ. Replay ran against the snapshot.',
      };
    default:
      return {
        short: code,
        description: `Unknown warning: ${code}`,
      };
  }
}
